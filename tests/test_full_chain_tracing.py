# -*- coding: utf-8 -*-
"""测试Full-Chain Tracing（PRD v1.9 / detailed-v2.2）"""

import pytest
import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.schemas import TaskGraph, Task, Step
from app.schemas.writeback import WritebackEnvelope, WritebackEvent, WritebackStatus
from app.audit import AuditEvent, AuditEventType, AuditLogger


def test_taskgraph_includes_trace_id():
    """测试TaskGraph包含trace_id"""
    trace_id = str(uuid.uuid4())
    
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="task_001",
                branch_id="main",
                steps=[
                    Step(
                        step_id="step_001",
                        domain="vehicle",
                        action={"action": "window_open", "level": "L1"}
                    )
                ]
            )
        ],
        session_id="session_001",
        trace_id=trace_id,
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    assert taskgraph.trace_id == trace_id
    
    # 验证序列化后包含trace_id
    json_data = json.loads(taskgraph.model_dump_json())
    assert json_data["trace_id"] == trace_id


def test_writeback_includes_trace_id():
    """测试WritebackEnvelope包含trace_id"""
    trace_id = str(uuid.uuid4())
    
    writeback = WritebackEnvelope(
        task_id="task_001",
        step_id="step_001",
        branch_id="main",
        trace_id=trace_id,
        event=WritebackEvent.VEHICLE_ACK,
        status=WritebackStatus.SUCCESS,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    assert writeback.trace_id == trace_id
    
    # 验证序列化后包含trace_id
    json_data = json.loads(writeback.model_dump_json())
    assert json_data["trace_id"] == trace_id


def test_audit_event_structure():
    """测试AuditEvent结构（无dialogue transcript）"""
    trace_id = str(uuid.uuid4())
    session_id = "session_001"
    
    event = AuditEvent(
        trace_id=trace_id,
        session_id=session_id,
        event_type=AuditEventType.UTTERANCE_RECEIVED,
        timestamp=datetime.utcnow().isoformat() + "Z",
        metadata={"utterance_length": 10}
    )
    
    assert event.trace_id == trace_id
    assert event.session_id == session_id
    assert event.event_type == AuditEventType.UTTERANCE_RECEIVED
    
    # 验证没有对话内容字段
    event_dict = event.model_dump()
    assert "utterance" not in event_dict
    assert "dialogue" not in event_dict
    assert "transcript" not in event_dict
    
    # 验证只包含结构化元数据
    assert "metadata" in event_dict


def test_audit_event_types_coverage():
    """测试审计事件类型覆盖度"""
    required_events = {
        "utterance_received",
        "assemble_done",
        "planner_start",
        "planner_end",
        "dispatch",
        "dispatch_blocked",
        "confirm_request",
        "confirm_accepted",
        "confirm_declined",
        "confirm_timeout",
        "vehicle_ack",
        "nav_route_started",
        "nav_failed",
        "rag_hit",
        "rag_miss",
        "unsupported",
        "refusal",
        "rewrite",
        "cancel",
        "tts_emit",
        "memory_put",
        "memory_search",
        "memory_put_blocked",
        "memory_put_masked",
    }
    
    # 获取所有定义的事件类型
    defined_events = {e.value for e in AuditEventType}
    
    # 验证所有必需的事件类型都已定义
    missing = required_events - defined_events
    assert len(missing) == 0, f"Missing audit event types: {missing}"


def test_audit_logger_stores_events_in_memory():
    """测试AuditLogger在内存中存储事件"""
    audit_logger = AuditLogger(pg_store=None)
    
    trace_id = str(uuid.uuid4())
    session_id = "session_001"
    
    # 创建并发出事件
    event = audit_logger.create_event(
        trace_id=trace_id,
        session_id=session_id,
        event_type=AuditEventType.UTTERANCE_RECEIVED
    )
    
    # 查询事件
    events = audit_logger.get_by_trace_id(trace_id)
    
    assert len(events) == 1
    assert events[0].trace_id == trace_id
    assert events[0].event_type == AuditEventType.UTTERANCE_RECEIVED


def test_same_trace_id_across_dispatch_and_writeback():
    """测试同一个对话的dispatch和writeback使用相同trace_id"""
    trace_id = str(uuid.uuid4())
    
    # 模拟dispatch时的TaskGraph
    taskgraph = TaskGraph(
        tasks=[
            Task(
                task_id="task_001",
                branch_id="main",
                steps=[
                    Step(
                        step_id="step_001",
                        domain="vehicle",
                        action={"action": "window_open", "level": "L1"}
                    )
                ]
            )
        ],
        session_id="session_001",
        trace_id=trace_id,
        timestamp=datetime.utcnow().isoformat() + "Z"
    )
    
    # 模拟writeback时回显trace_id
    writeback = WritebackEnvelope(
        task_id=taskgraph.tasks[0].task_id,
        step_id=taskgraph.tasks[0].steps[0].step_id,
        branch_id=taskgraph.tasks[0].branch_id,
        trace_id=taskgraph.trace_id,  # Echo from TaskGraph
        event=WritebackEvent.VEHICLE_ACK,
        status=WritebackStatus.SUCCESS,
        ts=datetime.utcnow().isoformat() + "Z"
    )
    
    # 验证trace_id一致
    assert taskgraph.trace_id == writeback.trace_id == trace_id


def test_audit_events_ordered_by_timestamp():
    """测试审计事件按时间排序"""
    audit_logger = AuditLogger(pg_store=None)
    
    trace_id = str(uuid.uuid4())
    session_id = "session_001"
    
    # 按顺序发出多个事件
    events_in_order = [
        AuditEventType.UTTERANCE_RECEIVED,
        AuditEventType.ASSEMBLE_DONE,
        AuditEventType.PLANNER_START,
        AuditEventType.PLANNER_END,
        AuditEventType.DISPATCH,
        AuditEventType.VEHICLE_ACK
    ]
    
    for event_type in events_in_order:
        audit_logger.create_event(
            trace_id=trace_id,
            session_id=session_id,
            event_type=event_type
        )
    
    # 查询事件
    retrieved_events = audit_logger.get_by_trace_id(trace_id)
    
    # 验证事件顺序
    assert len(retrieved_events) == len(events_in_order)
    for i, event in enumerate(retrieved_events):
        assert event.event_type == events_in_order[i]


def test_no_dialogue_transcript_in_audit_payload():
    """测试审计事件payload中不包含对话transcript"""
    trace_id = str(uuid.uuid4())
    
    # 创建各种类型的审计事件
    event_types_to_test = [
        AuditEventType.UTTERANCE_RECEIVED,
        AuditEventType.PLANNER_END,
        AuditEventType.DISPATCH,
        AuditEventType.UNSUPPORTED,
        AuditEventType.TTS_EMIT
    ]
    
    for event_type in event_types_to_test:
        event = AuditEvent(
            trace_id=trace_id,
            session_id="session_001",
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
        
        # 验证event dict中不包含对话内容
        event_dict = event.model_dump()
        
        # 不应包含这些字段
        forbidden_fields = ["utterance", "dialogue", "transcript", "user_input", "assistant_response"]
        for field in forbidden_fields:
            assert field not in event_dict, f"Event {event_type} should not contain {field}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
