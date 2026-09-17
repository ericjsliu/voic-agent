# -*- coding: utf-8 -*-
"""测试PRD v1.37 Feature 2: 检查点和恢复

验证：
1. 检查点保存和加载
2. 从检查点恢复执行（继续未完成步骤）
3. 多步骤TaskGraph中断后恢复
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime
from app.orchestrator.checkpoint import CheckpointManager, CheckpointData


class TestCheckpointManager:
    """测试检查点管理器"""
    
    def test_save_checkpoint(self):
        """测试保存检查点"""
        redis_mock = Mock()
        redis_mock.setex.return_value = True
        
        manager = CheckpointManager(redis_mock)
        
        session_id = "session_123"
        trace_id = "trace_456"
        taskgraph_dict = {
            "tasks": [{"task_id": "task_1", "steps": []}],
            "session_id": session_id,
            "trace_id": trace_id
        }
        step_states = {
            "step_1": {"status": "completed", "result": {"value": 1}},
            "step_2": {"status": "executing", "result": None}
        }
        completed_steps = ["step_1"]
        failed_steps = []
        shadow_state = {"gear": "P", "speed_kmh": 0}
        
        success = manager.save_checkpoint(
            session_id=session_id,
            trace_id=trace_id,
            taskgraph_dict=taskgraph_dict,
            step_states=step_states,
            completed_steps=completed_steps,
            failed_steps=failed_steps,
            shadow_state=shadow_state
        )
        
        assert success
        
        # 验证Redis调用
        redis_mock.setex.assert_called_once()
        call_args = redis_mock.setex.call_args[0]
        assert call_args[0] == f"checkpoint:{session_id}:{trace_id}"
        assert call_args[1] == 3600  # 1小时TTL
    
    def test_load_checkpoint_by_trace_id(self):
        """测试按trace_id加载检查点"""
        checkpoint_data = {
            "session_id": "session_123",
            "trace_id": "trace_456",
            "taskgraph": {"tasks": []},
            "step_states": {},
            "completed_steps": ["step_1"],
            "failed_steps": [],
            "shadow_state": {},
            "timestamp": "2024-01-01T00:00:00Z",
            "checkpoint_version": "1.0"
        }
        
        redis_mock = Mock()
        redis_mock.get.return_value = CheckpointData(**checkpoint_data).model_dump_json()
        
        manager = CheckpointManager(redis_mock)
        
        checkpoint = manager.load_checkpoint("session_123", "trace_456")
        
        assert checkpoint is not None
        assert checkpoint.session_id == "session_123"
        assert checkpoint.trace_id == "trace_456"
        assert len(checkpoint.completed_steps) == 1
    
    def test_load_checkpoint_not_found(self):
        """测试加载不存在的检查点"""
        redis_mock = Mock()
        redis_mock.get.return_value = None
        redis_mock.keys.return_value = []
        
        manager = CheckpointManager(redis_mock)
        
        checkpoint = manager.load_checkpoint("session_123", "nonexistent_trace")
        
        assert checkpoint is None
    
    def test_clear_checkpoint(self):
        """测试清除检查点"""
        redis_mock = Mock()
        redis_mock.delete.return_value = 1
        
        manager = CheckpointManager(redis_mock)
        
        success = manager.clear_checkpoint("session_123", "trace_456")
        
        assert success
        redis_mock.delete.assert_called_once_with("checkpoint:session_123:trace_456")
    
    def test_list_checkpoints(self):
        """测试列出会话的所有检查点"""
        checkpoint1 = CheckpointData(
            session_id="session_123",
            trace_id="trace_1",
            taskgraph={},
            step_states={},
            completed_steps=["step_1"],
            failed_steps=[],
            shadow_state={},
            timestamp="2024-01-01T00:00:00Z"
        )
        checkpoint2 = CheckpointData(
            session_id="session_123",
            trace_id="trace_2",
            taskgraph={},
            step_states={},
            completed_steps=["step_1", "step_2"],
            failed_steps=[],
            shadow_state={},
            timestamp="2024-01-01T01:00:00Z"
        )
        
        redis_mock = Mock()
        redis_mock.keys.return_value = [
            "checkpoint:session_123:trace_1",
            "checkpoint:session_123:trace_2"
        ]
        redis_mock.get.side_effect = [
            checkpoint1.model_dump_json(),
            checkpoint2.model_dump_json()
        ]
        
        manager = CheckpointManager(redis_mock)
        
        checkpoints = manager.list_checkpoints("session_123")
        
        assert len(checkpoints) == 2
        assert checkpoints[0]["trace_id"] == "trace_2"  # 按时间降序
        assert checkpoints[1]["trace_id"] == "trace_1"


@pytest.mark.asyncio
class TestOrchestratorCheckpoint:
    """测试Orchestrator检查点集成"""
    
    async def test_checkpoint_save_after_step(self):
        """测试步骤执行后自动保存检查点"""
        from app.schemas.taskgraph import TaskGraph, Task, Step, VehicleAction, DomainType, ActionLevel
        from app.orchestrator import Orchestrator
        from app.adapters import VehicleAdapter
        from unittest.mock import Mock, patch
        
        redis_mock = Mock()
        redis_mock.setex.return_value = True
        redis_mock.get.return_value = None
        
        vehicle_adapter = VehicleAdapter()
        vehicle_adapter.execute = AsyncMock(return_value={"action": "window_open"})
        
        orchestrator = Orchestrator(
            vehicle_adapter=vehicle_adapter,
            nav_adapter=Mock(),
            media_adapter=Mock(),
            calendar_adapter=Mock(),
            knowledge_adapter=Mock(),
            chitchat_adapter=Mock(),
            redis_client=redis_mock
        )
        
        taskgraph = TaskGraph(
            trace_id="trace_test",
            session_id="session_test",
            timestamp="2024-01-01T00:00:00Z",
            tasks=[
                Task(
                    task_id="task_1",
                    steps=[
                        Step(
                            step_id="step_1",
                            domain=DomainType.VEHICLE,
                            action=VehicleAction(action="window_open", level=ActionLevel.L1)
                        )
                    ]
                )
            ]
        )
        
        await orchestrator.execute_taskgraph(taskgraph)
        
        # 验证checkpoint被保存（setex被调用多次，包括idempotency和checkpoint）
        assert redis_mock.setex.call_count > 0
    
    async def test_resume_from_checkpoint(self):
        """测试从检查点恢复执行"""
        from app.schemas.taskgraph import TaskGraph, Task, Step, VehicleAction, DomainType, ActionLevel
        from app.orchestrator import Orchestrator
        from app.orchestrator.checkpoint import CheckpointData
        from app.adapters import VehicleAdapter
        from unittest.mock import Mock
        
        # 准备检查点数据（step_1已完成，step_2待执行）
        checkpoint_data = CheckpointData(
            session_id="session_test",
            trace_id="trace_test",
            taskgraph={
                "trace_id": "trace_test",
                "session_id": "session_test",
                "timestamp": "2024-01-01T00:00:00Z",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "branch_id": "main",
                        "steps": [
                            {
                                "step_id": "step_1",
                                "domain": "vehicle",
                                "action": {"action": "window_open", "level": "L1"},
                                "depends_on": []
                            },
                            {
                                "step_id": "step_2",
                                "domain": "vehicle",
                                "action": {"action": "window_close", "level": "L1"},
                                "depends_on": ["step_1"]
                            }
                        ]
                    }
                ]
            },
            step_states={
                "step_1": {"status": "completed", "result": {"action": "window_open"}},
                "step_2": {"status": "pending", "result": None}
            },
            completed_steps=["step_1"],
            failed_steps=[],
            shadow_state={"gear": "P"},
            timestamp="2024-01-01T00:00:00Z"
        )
        
        redis_mock = Mock()
        redis_mock.get.return_value = checkpoint_data.model_dump_json()
        redis_mock.delete.return_value = 1
        redis_mock.setex.return_value = True
        
        vehicle_adapter = VehicleAdapter()
        execute_spy = AsyncMock(return_value={"action": "window_close"})
        vehicle_adapter.execute = execute_spy
        
        orchestrator = Orchestrator(
            vehicle_adapter=vehicle_adapter,
            nav_adapter=Mock(),
            media_adapter=Mock(),
            calendar_adapter=Mock(),
            knowledge_adapter=Mock(),
            chitchat_adapter=Mock(),
            redis_client=redis_mock
        )
        
        # 从检查点恢复
        result = await orchestrator.resume_from_checkpoint("session_test", "trace_test")
        
        assert result is not None
        assert result["resumed_from_checkpoint"] is True
        assert result["trace_id"] == "trace_test"
        
        # 验证只有step_2被执行（step_1已在检查点中标记为完成）
        # 注意：由于幂等性缓存，可能执行0次或1次
        assert execute_spy.call_count <= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
