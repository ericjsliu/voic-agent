# -*- coding: utf-8 -*-
"""P0 merge-blocking fixes - simplified tests"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from app.schemas.taskgraph import TaskGraph, Task, Step, DomainType, ActionLevel, VehicleAction, ChitchatAction
from app.schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from app.orchestrator.orchestrator import Orchestrator
from app.planner.capability_wrapper import CapabilityAwarePlanner
from app.capabilities.schema import CapabilityProfile, ActionCapability


# ==================== P0 Fix #1: L2 after confirm → vehicle_ack ====================

def test_orchestrator_has_mqtt_publish_callback():
    """测试Orchestrator有MQTT publish callback属性"""
    orchestrator = Orchestrator(
        vehicle_adapter=AsyncMock(),
        nav_adapter=AsyncMock(),
        media_adapter=AsyncMock(),
        calendar_adapter=AsyncMock(),
        knowledge_adapter=AsyncMock(),
        chitchat_adapter=AsyncMock()
    )
    
    # 验证有mqtt_publish_callback属性
    assert hasattr(orchestrator, 'mqtt_publish_callback')
    assert orchestrator.mqtt_publish_callback is None
    
    # 验证可以设置
    mock_callback = MagicMock()
    orchestrator.mqtt_publish_callback = mock_callback
    assert orchestrator.mqtt_publish_callback == mock_callback
    
    # 验证有pending_l2_publish属性
    assert hasattr(orchestrator, 'pending_l2_publish')
    assert orchestrator.pending_l2_publish is None


def test_orchestrator_has_l2_publish_methods():
    """测试Orchestrator has方法来获取L2发布"""
    orchestrator = Orchestrator(
        vehicle_adapter=AsyncMock(),
        nav_adapter=AsyncMock(),
        media_adapter=AsyncMock(),
        calendar_adapter=AsyncMock(),
        knowledge_adapter=AsyncMock(),
        chitchat_adapter=AsyncMock()
    )
    
    # 验证有get_and_clear_pending_l2方法
    assert hasattr(orchestrator, 'get_and_clear_pending_l2')
    assert callable(orchestrator.get_and_clear_pending_l2)


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
        supported_actions=["window_open"],
        action_capabilities={
            "window_open": ActionCapability(
                action="window_open",
                description="控制车窗"
            )
        }
    )
    
    # 创建TaskGraph with mixed supported/unsupported steps
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t_mixed",
                branch_id="main",
                steps=[
                    # Supported action
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
                    # Unsupported action
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
    )
    
    # Create capability-aware planner
    mock_audit = MagicMock()
    mock_audit.create_event = MagicMock()
    planner = CapabilityAwarePlanner(base_planner=None, audit_logger=mock_audit)
    
    # Apply validation directly
    result = planner._validate_against_profile(taskgraph, profile, "tr_mixed", "sess_test")
    
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
    assert unsupported_steps[0].domain == DomainType.CHITCHAT
    
    # 验证audit event被触发
    mock_audit.create_event.assert_called()


@pytest.mark.asyncio
async def test_all_unsupported_returns_only_tts():
    """测试所有动作都不支持时，只返回TTS"""
    
    # Empty capability profile
    profile = CapabilityProfile(
        model_id="model_empty",
        model_name="Empty Model",
        supported_actions=[]
    )
    
    taskgraph = TaskGraph(
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
    )
    
    planner = CapabilityAwarePlanner(base_planner=None, audit_logger=MagicMock())
    
    result = planner._validate_against_profile(taskgraph, profile, "tr_all_unsupported", "sess_test")
    
    # 验证：只有unsupported TTS
    assert len(result.tasks) == 1
    assert len(result.tasks[0].steps) == 1
    assert result.tasks[0].steps[0].step_id == "s_unsupported"
    assert result.tasks[0].steps[0].domain == DomainType.CHITCHAT


# ==================== Unified Status Enum Test ====================

def test_writeback_status_enum_unified():
    """测试WritebackStatus枚举统一为accepted/rejected/failed"""
    
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
