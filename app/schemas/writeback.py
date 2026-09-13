# -*- coding: utf-8 -*-
"""Writeback envelope schema"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class WritebackEvent(str, Enum):
    """写回事件类型"""
    VEHICLE_ACK = "vehicle_ack"
    CONFIRM_RESULT = "confirm_result"
    NAV_ROUTE_STARTED = "nav_route_started"
    NAV_ARRIVED = "nav_arrived"
    NAV_REROUTED = "nav_rerouted"
    MEDIA_ACK = "media_ack"
    CALENDAR_ACK = "calendar_ack"
    KNOWLEDGE_DONE = "knowledge_done"


class WritebackStatus(str, Enum):
    """写回状态"""
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"  # 车辆拒绝（如档位不在P）
    ACCEPTED = "accepted"  # 用户确认接受
    DECLINED = "declined"  # 用户确认拒绝
    TIMEOUT = "timeout"  # 确认超时
    PENDING = "pending"  # L2等待确认中


class WritebackEnvelope(BaseModel):
    """写回信封（车辆上行消息）"""
    task_id: str
    step_id: str
    branch_id: str
    trace_id: str  # PRD v1.9 / detailed-v2.2: full-chain tracing
    event: WritebackEvent
    status: WritebackStatus
    reason: Optional[str] = None  # 失败/拒绝原因
    ts: str  # ISO 8601 时间戳
