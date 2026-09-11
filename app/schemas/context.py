# -*- coding: utf-8 -*-
"""Dialogue context schemas"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class SessionInfo(BaseModel):
    """会话信息"""
    session_id: str
    driver_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    created_at: str  # ISO 8601
    last_active: str  # ISO 8601


class DialogueContext(BaseModel):
    """对话上下文"""
    session_info: SessionInfo
    recent_utterances: List[str] = []  # 最近N条用户话语
    shadow_state: Dict[str, Any] = {}  # 影子状态（车辆当前状态）
    memory_slice: Dict[str, Any] = {}  # 记忆切片（用户偏好等）
    current_location: Optional[Dict[str, float]] = None  # {"lat": x, "lon": y}
