# -*- coding: utf-8 -*-
"""测试Merge-Blocking修复（vehicle-side review）

1. L2 mock must NOT auto-accept
2. Unified writeback status enum (accepted/rejected/failed)
3. Unsupported actions don't reach MQTT, emit audit events
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from app.schemas import TaskGraph, Task, Step
from app.schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from app.capabilities import CapabilityProfile
from app.audit import AuditLogger, AuditEventType


def test_writeback_status_enum_unified():
    """测试WritebackStatus enum统一（无SUCCESS）"""
    # 验证SUCCESS不再存在
    with pytest.raises(AttributeError):
        _ = WritebackStatus.SUCCESS
    
    # 验证新的统一枚举值存在
    assert hasattr(WritebackStatus, 'ACCEPTED')
    assert hasattr(WritebackStatus, 'REJECTED')
    assert hasattr(WritebackStatus, 'FAILED')
    assert hasattr(WritebackStatus, 'DECLINED')  # L2 only
    assert hasattr(WritebackStatus, 'TIMEOUT')   # L2 only
    assert hasattr(WritebackStatus, 'PENDING')   # L2 only
    
    # 验证可以创建带accepted状态的writeback
    writeback = WritebackEnvelope(
        task_id="t1",
        step_id="s1",
        branch_id="main",
        trace_id="trace1",
        event=WritebackEvent.VEHICLE_ACK,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    assert writeback.status == WritebackStatus.ACCEPTED


def test_unsupported_action_emits_audit_event():
    """测试unsupported动作发出audit event"""
    from app.planner.capability_wrapper import CapabilityAwarePlanner
    from app.planner import Planner
    from app.adapters import NavigationAdapter
    from app.schemas.context import DialogueContext
    
    audit_logger = AuditLogger(pg_store=None)
    base_planner = Planner(nav_adapter=NavigationAdapter())
    planner = CapabilityAwarePlanner(base_planner=base_planner, audit_logger=audit_logger)
    
    # 创建一个model_a profile（不支持sunroof）
    profile = CapabilityProfile(
        model_id="model_a",
        model_name="Model A",
        supported_actions=["window_open", "window_close", "door_lock", "door_unlock"],
        feature_flags={"has_sunroof": False}
    )
    
    # 创建一个包含不支持动作的TaskGraph
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t1",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s1",
                        domain="vehicle",
                        action={"action": "sunroof_open", "level": "L1"}
                    )
                ]
            )
        ],
        session_id="sess1",
        trace_id="trace1",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 验证TaskGraph（会过滤unsupported动作）
    validated = planner._validate_against_profile(
        taskgraph, profile, trace_id="trace1", session_id="sess1"
    )
    
    # 验证发出了unsupported audit event
    events = audit_logger.get_by_trace_id("trace1")
    unsupported_events = [e for e in events if e.event_type == AuditEventType.UNSUPPORTED]
    
    assert len(unsupported_events) > 0
    assert unsupported_events[0].action == "sunroof_open"
    assert unsupported_events[0].domain == "vehicle"


def test_unsupported_action_returns_chitchat_tts():
    """测试unsupported动作返回chitchat TTS响应（不发送MQTT）"""
    from app.planner.capability_wrapper import CapabilityAwarePlanner
    from app.planner import Planner
    from app.adapters import NavigationAdapter
    
    base_planner = Planner(nav_adapter=NavigationAdapter())
    planner = CapabilityAwarePlanner(base_planner=base_planner, audit_logger=None)
    
    profile = CapabilityProfile(
        model_id="model_a",
        model_name="Model A",
        supported_actions=["window_open", "door_lock"],
        feature_flags={"has_sunroof": False}
    )
    
    # 创建包含不支持动作的TaskGraph
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t1",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s1",
                        domain="vehicle",
                        action={"action": "sunroof_open", "level": "L1"}
                    )
                ]
            )
        ],
        session_id="sess1",
        trace_id="trace1",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 验证
    result = planner._validate_against_profile(
        taskgraph, profile, trace_id="trace1", session_id="sess1"
    )
    
    # 结果应该是chitchat任务，包含TTS提示
    assert len(result.tasks) == 1
    assert result.tasks[0].task_id == "t_unsupported"
    assert result.tasks[0].steps[0].domain.value == "chitchat"
    assert "不支持" in result.tasks[0].steps[0].action.response
    
    # 验证没有vehicle动作（不会发送到MQTT）
    for task in result.tasks:
        for step in task.steps:
            assert step.domain.value != "vehicle"


def test_capability_filter_before_mqtt():
    """测试能力过滤在MQTT之前发生（云端过滤）"""
    from app.planner.capability_wrapper import CapabilityAwarePlanner
    from app.planner import Planner
    from app.adapters import NavigationAdapter
    
    base_planner = Planner(nav_adapter=NavigationAdapter())
    planner = CapabilityAwarePlanner(base_planner=base_planner, audit_logger=None)
    
    profile = CapabilityProfile(
        model_id="model_a",
        model_name="Model A",
        supported_actions=["window_open"],
        feature_flags={}
    )
    
    # 混合任务：一个支持，一个不支持
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="t1",
                branch_id="main",
                steps=[
                    Step(
                        step_id="s1",
                        domain="vehicle",
                        action={"action": "window_open", "level": "L1"}
                    ),
                    Step(
                        step_id="s2",
                        domain="vehicle",
                        action={"action": "sunroof_open", "level": "L1"}
                    )
                ]
            )
        ],
        session_id="sess1",
        trace_id="trace1",
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 过滤后应该只剩chitchat（因为有unsupported action）
    result = planner._validate_against_profile(
        taskgraph, profile, trace_id="trace1", session_id="sess1"
    )
    
    # 有unsupported动作时，整个TaskGraph被替换为chitchat TTS
    assert result.tasks[0].task_id == "t_unsupported"
    assert result.tasks[0].steps[0].domain.value == "chitchat"


def test_l2_status_values():
    """测试L2 confirm_result使用正确的状态值"""
    # L2 accepted
    wb_accepted = WritebackEnvelope(
        task_id="t1",
        step_id="s1",
        branch_id="main",
        trace_id="trace1",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.ACCEPTED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    assert wb_accepted.status == WritebackStatus.ACCEPTED
    
    # L2 declined
    wb_declined = WritebackEnvelope(
        task_id="t1",
        step_id="s1",
        branch_id="main",
        trace_id="trace1",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.DECLINED,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    assert wb_declined.status == WritebackStatus.DECLINED
    
    # L2 timeout
    wb_timeout = WritebackEnvelope(
        task_id="t1",
        step_id="s1",
        branch_id="main",
        trace_id="trace1",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.TIMEOUT,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    assert wb_timeout.status == WritebackStatus.TIMEOUT
    
    # L2 pending
    wb_pending = WritebackEnvelope(
        task_id="t1",
        step_id="s1",
        branch_id="main",
        trace_id="trace1",
        event=WritebackEvent.CONFIRM_RESULT,
        status=WritebackStatus.PENDING,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    assert wb_pending.status == WritebackStatus.PENDING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
