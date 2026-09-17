# -*- coding: utf-8 -*-
"""测试PRD v1.37 Feature 1: tool_use_id幂等性

验证：
1. tool_use_id生成稳定性（相同trace+step → 相同ID）
2. 结果缓存和命中
3. 重复执行返回缓存结果，不触发副作用
"""

import pytest
import hashlib
from unittest.mock import Mock, AsyncMock, patch
from app.orchestrator.idempotency import IdempotencyManager


class TestIdempotencyManager:
    """测试幂等性管理器"""
    
    def test_generate_tool_use_id_stability(self):
        """测试tool_use_id生成稳定性"""
        redis_mock = Mock()
        manager = IdempotencyManager(redis_mock)
        
        trace_id = "trace_123"
        step_id = "step_1"
        
        # 多次生成应该返回相同ID
        id1 = manager.generate_tool_use_id(trace_id, step_id)
        id2 = manager.generate_tool_use_id(trace_id, step_id)
        
        assert id1 == id2
        assert len(id1) == 64  # SHA256十六进制
        
        # 不同的trace_id或step_id应该生成不同ID
        id3 = manager.generate_tool_use_id("trace_456", step_id)
        id4 = manager.generate_tool_use_id(trace_id, "step_2")
        
        assert id1 != id3
        assert id1 != id4
    
    def test_tool_use_id_matches_sha256(self):
        """验证tool_use_id是trace_id+step_id的SHA256哈希"""
        redis_mock = Mock()
        manager = IdempotencyManager(redis_mock)
        
        trace_id = "trace_123"
        step_id = "step_1"
        
        expected = hashlib.sha256(f"{trace_id}:{step_id}".encode('utf-8')).hexdigest()
        actual = manager.generate_tool_use_id(trace_id, step_id)
        
        assert actual == expected
    
    def test_cache_and_get_result(self):
        """测试结果缓存和获取"""
        redis_mock = Mock()
        redis_mock.get.return_value = '{"status": "completed", "result": {"value": 42}}'
        redis_mock.setex.return_value = True
        
        manager = IdempotencyManager(redis_mock)
        
        tool_use_id = "test_tool_use_id"
        result = {
            "status": "completed",
            "result": {"value": 42}
        }
        
        # 缓存结果
        success = manager.cache_result(tool_use_id, result)
        assert success
        
        # 验证Redis调用
        redis_mock.setex.assert_called_once()
        call_args = redis_mock.setex.call_args[0]
        assert call_args[0] == f"idempotency:{tool_use_id}"
        assert call_args[1] == 3600 * 24  # 24小时TTL
        
        # 获取缓存结果
        cached = manager.get_cached_result(tool_use_id)
        assert cached == result
    
    def test_cache_miss(self):
        """测试缓存未命中"""
        redis_mock = Mock()
        redis_mock.get.return_value = None
        
        manager = IdempotencyManager(redis_mock)
        
        cached = manager.get_cached_result("nonexistent_id")
        assert cached is None
    
    def test_clear_cache(self):
        """测试清除缓存"""
        redis_mock = Mock()
        redis_mock.delete.return_value = 1  # Redis delete返回删除的键数量
        
        manager = IdempotencyManager(redis_mock)
        
        tool_use_id = "test_tool_use_id"
        success = manager.clear_cache(tool_use_id)
        
        assert success
        redis_mock.delete.assert_called_once_with(f"idempotency:{tool_use_id}")


@pytest.mark.asyncio
class TestOrchestratorIdempotency:
    """测试Orchestrator集成幂等性"""
    
    async def test_duplicate_step_execution_uses_cache(self):
        """测试重复步骤执行使用缓存，不重复执行副作用"""
        from app.schemas.taskgraph import TaskGraph, Task, Step, VehicleAction, DomainType, ActionLevel
        from app.orchestrator import Orchestrator
        from app.adapters import VehicleAdapter
        from unittest.mock import Mock
        
        # Mock Redis
        redis_mock = Mock()
        cached_result = '{"status": "completed", "result": {"action": "window_open"}}'
        redis_mock.get.return_value = None  # 第一次未命中
        redis_mock.setex.return_value = True
        
        # Mock适配器（记录执行次数）
        vehicle_adapter = VehicleAdapter()
        execute_spy = AsyncMock(return_value={"action": "window_open"})
        vehicle_adapter.execute = execute_spy
        
        # 创建Orchestrator
        orchestrator = Orchestrator(
            vehicle_adapter=vehicle_adapter,
            nav_adapter=Mock(),
            media_adapter=Mock(),
            calendar_adapter=Mock(),
            knowledge_adapter=Mock(),
            chitchat_adapter=Mock(),
            redis_client=redis_mock
        )
        
        # 创建TaskGraph
        trace_id = "trace_test_123"
        taskgraph = TaskGraph(
            trace_id=trace_id,
            session_id="session_test",
            timestamp="2024-01-01T00:00:00Z",
            tasks=[
                Task(
                    task_id="task_1",
                    steps=[
                        Step(
                            step_id="step_1",
                            domain=DomainType.VEHICLE,
                            action=VehicleAction(
                                action="window_open",
                                target="all",
                                level=ActionLevel.L1
                            )
                        )
                    ]
                )
            ]
        )
        
        # 第一次执行
        result1 = await orchestrator.execute_taskgraph(taskgraph)
        
        # 验证适配器被调用
        assert execute_spy.call_count == 1
        
        # 验证结果被缓存
        assert redis_mock.setex.call_count > 0
        
        # 模拟缓存命中
        redis_mock.get.return_value = cached_result
        
        # 第二次执行相同步骤（应该使用缓存）
        result2 = await orchestrator.execute_taskgraph(taskgraph)
        
        # 验证适配器没有再次被调用（幂等性）
        assert execute_spy.call_count == 1  # 仍然是1次，没有增加
    
    async def test_different_steps_different_cache(self):
        """测试不同步骤使用不同缓存"""
        from app.orchestrator.idempotency import IdempotencyManager
        
        redis_mock = Mock()
        manager = IdempotencyManager(redis_mock)
        
        trace_id = "trace_123"
        step1_id = "step_1"
        step2_id = "step_2"
        
        # 不同步骤生成不同tool_use_id
        tool_use_id_1 = manager.generate_tool_use_id(trace_id, step1_id)
        tool_use_id_2 = manager.generate_tool_use_id(trace_id, step2_id)
        
        assert tool_use_id_1 != tool_use_id_2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
