# -*- coding: utf-8 -*-
"""Audit Events for Full-Chain Tracing (PRD v1.9 / detailed-v2.2)

Emit structured audit events (NO dialogue transcript) for tracing and debugging.
"""

from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class AuditEventType(str, Enum):
    """审计事件类型"""
    UTTERANCE_RECEIVED = "utterance_received"
    ASSEMBLE_DONE = "assemble_done"
    PLANNER_START = "planner_start"
    PLANNER_END = "planner_end"
    SCHEMA_REPAIR = "schema_repair"
    MODEL_TIER_CHANGE = "model_tier_change"
    DISPATCH = "dispatch"
    DISPATCH_BLOCKED = "dispatch_blocked"
    CONFIRM_REQUEST = "confirm_request"
    CONFIRM_ACCEPTED = "confirm_accepted"
    CONFIRM_DECLINED = "confirm_declined"
    CONFIRM_TIMEOUT = "confirm_timeout"
    VEHICLE_ACK = "vehicle_ack"
    NAV_ROUTE_STARTED = "nav_route_started"
    NAV_FAILED = "nav_failed"
    NAV_ARRIVED = "nav_arrived"
    RAG_HIT = "rag_hit"
    RAG_MISS = "rag_miss"
    UNSUPPORTED = "unsupported"
    REFUSAL = "refusal"
    REWRITE = "rewrite"
    CANCEL = "cancel"
    TTS_EMIT = "tts_emit"
    # Stage 补丁：记忆事件
    MEMORY_PUT = "memory_put"
    MEMORY_SEARCH = "memory_search"
    MEMORY_PUT_BLOCKED = "memory_put_blocked"


class AuditEvent(BaseModel):
    """审计事件（无对话transcript）"""
    trace_id: str
    session_id: str
    event_type: AuditEventType
    timestamp: str  # ISO 8601
    
    # 可选字段（根据event_type不同而不同）
    task_id: Optional[str] = None
    step_id: Optional[str] = None
    branch_id: Optional[str] = None
    domain: Optional[str] = None
    action: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    model_name: Optional[str] = None
    duration_ms: Optional[int] = None
    citations_count: Optional[int] = None
    
    # 额外元数据（非敏感信息）
    metadata: Optional[Dict[str, Any]] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() + "Z"
        }
