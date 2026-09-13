# -*- coding: utf-8 -*-
"""P0 merge-blocking fixes tests"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.schemas.taskgraph import TaskGraph, Task, Step, DomainType, ActionLevel
from app.schemas.actions import VehicleAction, NavigationAction
from app.schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from app.orchestrator.orchestrator import Orchestrator
from app.planner.capability_wrapper import CapabilityAwarePlanner
from app.capabilities.schema import CapabilityProfile, ActionCapability


# ==================== P0 Fix #1: L2 after confirm → vehicle_ack ====================

@pytest.mark.asyncio
async def test_l2_confirmed_publishes_to_mqtt():
    """测试L2确认后发布到MQTT而不是立即完成"""
    
    # 创建mock adapters
    mock_vehicle_adapter = AsyncMock()
    mock_vehicle_adapter.execute = AsyncMock(return_value={"status": "ok"})
    
    # 创建orchestrator
    orchestrator = Orchestrator(
        vehicle_adapter=mock_vehicle_adapter,
        nav_adapter=AsyncMock(),
        media_adapter=AsyncMock(),
        calendar_adapter=AsyncMock(),
        knowledge_adapter=AsyncMock(),
        chitchat_adapter=AsyncMock()
    )
    
    # 设置MQTT publish callback
    published_graphs = []
    def mock_publish(tg: TaskGraph):
        published_graphs.append(tg)
    orchestrator.mqtt_publish_callback = mock_publish
    
    # 创建L2 TaskGraph
    l2_taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_l2_test",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_l2",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L2,
                            target="all",
                            percent=100
                        ),
                        description="Open all windows"
                    )
                ]
            )
        ],
        session_id="sess_test",
        trace_id="tr_test123",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 执行TaskGraph
    await orchestrator.execute_taskgraph(l2_taskgraph, initial_publish=False)
    
    # 发送L2 confirm_result=accepted
    confirm_writeback = WritebackEnvelope(
        task_id="t_l2_test",
        step_id="s_l2",
        branch_id="main",
        trace_id="tr_test123",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.handle_writeback(confirm_writeback)
    
    # 验证：应该发布L2步骤到MQTT
    assert len(published_graphs) == 1
    published = published_graphs[0]
    assert published.metadata.get("l2_confirmed") is True
    assert published.tasks[0].steps[0].step_id == "s_l2"
    
    # 验证：step应该是EXECUTING，等待vehicle_ack
    task_state = orchestrator.task_states["t_l2_test"]
    step_state = task_state.step_states["s_l2"]
    assert step_state.status.value == "executing"  # Not completed yet
    
    # 现在发送vehicle_ack
    vehicle_ack = WritebackEnvelope(
        task_id="t_l2_test",
        step_id="s_l2",
        branch_id="main",
        trace_id="tr_test123",
        event=WritebackEvent.VEHICLE_ACK,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.handle_writeback(vehicle_ack)
    
    # 现在应该完成
    assert step_state.status.value == "completed"


@pytest.mark.asyncio
async def test_l2_timeout_does_not_publish():
    """测试L2超时或拒绝不发布到MQTT"""
    
    orchestrator = Orchestrator(
        vehicle_adapter=AsyncMock(),
        nav_adapter=AsyncMock(),
        media_adapter=AsyncMock(),
        calendar_adapter=AsyncMock(),
        knowledge_adapter=AsyncMock(),
        chitchat_adapter=AsyncMock()
    )
    
    published_graphs = []
    orchestrator.mqtt_publish_callback = lambda tg: published_graphs.append(tg)
    
    l2_taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_l2_timeout",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_l2",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L2,
                            target="all",
                            percent=100
                        )
                    )
                ]
            )
        ],
        session_id="sess_test",
        trace_id="tr_test456",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.execute_taskgraph(l2_taskgraph, initial_publish=False)
    
    # 发送declined
    declined_writeback = WritebackEnvelope(
        task_id="t_l2_timeout",
        step_id="s_l2",
        branch_id="main",
        trace_id="tr_test456",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.DECLINED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    await orchestrator.handle_writeback(declined_writeback)
    
    # 验证：没有发布到MQTT
    assert len(published_graphs) == 0
    
    # 验证：step被取消
    task_state = orchestrator.task_states["t_l2_timeout"]
    step_state = task_state.step_states["s_l2"]
    assert step_state.status.value == "cancelled"


# ==================== P0 Fix #2: WS l2_confirm must include trace_id ====================

def test_writeback_envelope_accepts_trace_id():
    """测试WritebackEnvelope可以接受trace_id"""
    
    writeback = WritebackEnvelope(
        task_id="t_test",
        step_id="s_test",
        branch_id="main",
        trace_id="tr_abc123",  # Must be accepted
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    assert writeback.trace_id == "tr_abc123"


# ==================== P0 Fix #3: Mixed utterance - keep what works ====================

@pytest.mark.asyncio
async def test_mixed_utterance_keeps_supported_actions():
    """测试混合指令保留支持的动作，只过滤不支持的"""
    
    # 创建能力档案：只支持window_open，不支持sunroof_open
    profile = CapabilityProfile(
        model_id="model_a",
        model_name="Model A",
        supported_actions={
            "window_open": ActionCapability(
                action="window_open",
                display_name="打开车窗",
                description="控制车窗"
            )
        },
        feature_flags={}
    )
    
    # 创建mock planner
    mock_base_planner = AsyncMock()
    mock_base_planner.plan = AsyncMock(return_value=TaskGraph(
        tasks=[
            Task(
                task_id="t_mixed",
                branch_id="main",
                steps=[
                    # 支持的动作
                    Step(
                        step_id="s_window",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="window_open",
                            level=ActionLevel.L0,
                            target="all"
                        ),
                        description="Open windows"
                    ),
                    # 不支持的动作
                    Step(
                        step_id="s_sunroof",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="sunroof_open",
                            level=ActionLevel.L0,
                            percent=100
                        ),
                        description="Open sunroof"
                    )
                ]
            )
        ],
        session_id="sess_test",
        trace_id="tr_mixed",
        timestamp=datetime.utcnow().isoformat() + "Z"
    ))
    
    # 创建capability-aware planner
    mock_audit = MagicMock()
    mock_audit.create_event = MagicMock()
    planner = CapabilityAwarePlanner(base_planner=mock_base_planner, audit_logger=mock_audit)
    
    # 规划
    from app.schemas.context import DialogueContext
    result = await planner.plan(
        user_utterance="打开车窗和天窗",
        context=DialogueContext(
            session_id="sess_test",
            driver_name="test",
            history=[],
            memory_slice={}
        ),
        capability_profile=profile,
        trace_id="tr_mixed",
        session_id="sess_test"
    )
    
    # 验证：应该保留window_open步骤
    assert len(result.tasks) == 1
    steps = result.tasks[0].steps
    
    # 应该有window_open + unsupported TTS
    step_ids = [s.step_id for s in steps]
    assert "s_window" in step_ids, "Supported step should be kept"
    
    # 不应该有sunroof步骤
    assert "s_sunroof" not in step_ids, "Unsupported step should be removed"
    
    # 应该有unsupported TTS步骤
    unsupported_steps = [s for s in steps if s.step_id == "s_unsupported"]
    assert len(unsupported_steps) == 1, "Should add unsupported TTS notice"
    
    # 验证audit event被触发
    mock_audit.create_event.assert_called()
    call_args = mock_audit.create_event.call_args
    assert call_args[1]["action"] == "sunroof_open"


@pytest.mark.asyncio
async def test_all_unsupported_returns_only_tts():
    """测试所有动作都不支持时，只返回TTS"""
    
    # 空能力档案
    profile = CapabilityProfile(
        model_id="model_empty",
        model_name="Empty Model",
        supported_actions={},
        feature_flags={}
    )
    
    mock_base_planner = AsyncMock()
    mock_base_planner.plan = AsyncMock(return_value=TaskGraph(
        tasks=[
            Task(
                task_id="t_all_unsupported",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s_sunroof",
                        domain=DomainType.VEHICLE,
                        action=VehicleAction(
                            action="sunroof_open",
                            level=ActionLevel.L0,
                            percent=100
                        )
                    )
                ]
            )
        ],
        session_id="sess_test",
        trace_id="tr_all_unsupported",
        timestamp=datetime.utcnow().isoformat() + "Z"
    ))
    
    planner = CapabilityAwarePlanner(base_planner=mock_base_planner, audit_logger=MagicMock())
    
    from app.schemas.context import DialogueContext
    result = await planner.plan(
        user_utterance="打开天窗",
        context=DialogueContext(
            session_id="sess_test",
            driver_name="test",
            history=[],
            memory_slice={}
        ),
        capability_profile=profile,
        trace_id="tr_all_unsupported",
        session_id="sess_test"
    )
    
    # 验证：只有unsupported TTS
    assert len(result.tasks) == 1
    assert len(result.tasks[0].steps) == 1
    assert result.tasks[0].steps[0].step_id == "s_unsupported"
    assert result.tasks[0].steps[0].domain == DomainType.CHITCHAT


# ==================== Unified Status Enum Test ====================

def test_writeback_status_enum_unified():
    """测试WritbackStatus枚举统一为accepted/rejected/failed（merge-blocking fix #2）"""
    
    # 验证正确的状态值
    assert WritebackStatus.ACCEPTED.value == "accepted"
    assert WritebackStatus.REJECTED.value == "rejected"
    assert WritebackStatus.FAILED.value == "failed"
    
    # L2特定状态
    assert WritebackStatus.PENDING.value == "pending"
    assert WritebackStatus.DECLINED.value == "declined"
    assert WritebackStatus.TIMEOUT.value == "timeout"
    
    # 确保没有SUCCESS（已移除）
    with pytest.raises(AttributeError):
        _ = WritebackStatus.SUCCESS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
