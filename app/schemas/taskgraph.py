# -*- coding: utf-8 -*-
"""TaskGraph schema definitions"""

from enum import Enum
from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, Field


class ActionLevel(str, Enum):
    """动作级别"""
    L0 = "L0"  # 立即执行，无风险（查询类）
    L1 = "L1"  # 立即执行，可撤销（控制类）
    L2 = "L2"  # 需要确认才执行（高风险操作）


class DomainType(str, Enum):
    """域类型"""
    VEHICLE = "vehicle"
    NAVIGATION = "navigation"
    MEDIA = "media"
    CALENDAR = "calendar"
    KNOWLEDGE = "knowledge"
    CHITCHAT = "chitchat"


# ==================== Vehicle Domain ====================
class VehicleAction(BaseModel):
    """车辆控制动作"""
    action: Literal[
        "window_open", "window_close",
        "door_lock", "door_unlock",
        "ac_on", "ac_off", "ac_set_temp",
        "sunroof_open", "sunroof_close",
        "seat_heat_on", "seat_heat_off",
        "trunk_open"
    ]
    target: Optional[str] = None  # 例如: "driver_window", "all_windows", "front_left"
    value: Optional[float] = None  # 用于 ac_set_temp (温度值)
    level: ActionLevel = ActionLevel.L1


# ==================== Navigation Domain ====================
class RouteStrategy(str, Enum):
    """导航策略"""
    FASTEST = "fastest"
    SHORTEST = "shortest"
    ECO = "eco"


class RoutePreferences(BaseModel):
    """导航偏好"""
    avoid_highway: bool = False
    avoid_toll: bool = False
    avoid_ferry: bool = False
    strategy: RouteStrategy = RouteStrategy.FASTEST
    via_points: List[str] = Field(default_factory=list)  # POI名称列表


class NavGoal(BaseModel):
    """导航目标（POI已解析）"""
    poi_name: str
    latitude: float
    longitude: float
    address: Optional[str] = None


class NavigationAction(BaseModel):
    """导航动作"""
    action: Literal["nav_to", "nav_cancel", "nav_reroute"]
    goal: Optional[NavGoal] = None
    route_prefs: RoutePreferences = Field(default_factory=RoutePreferences)
    level: ActionLevel = ActionLevel.L0  # nav_to 是 L0（查询路线），实际导航由车辆开始


# ==================== Media Domain ====================
class MediaAction(BaseModel):
    """媒体控制动作"""
    action: Literal[
        "play_music", "pause", "next_track", "prev_track",
        "volume_up", "volume_down", "set_volume",
        "play_radio", "search_music"
    ]
    query: Optional[str] = None  # 搜索关键词或歌曲名
    volume: Optional[int] = None  # 音量值 (0-100)
    station: Optional[str] = None  # 电台频率
    level: ActionLevel = ActionLevel.L1


# ==================== Calendar Domain ====================
class CalendarAction(BaseModel):
    """日历动作"""
    action: Literal[
        "query_schedule", "add_event", "cancel_event",
        "next_appointment", "today_schedule"
    ]
    event_title: Optional[str] = None
    start_time: Optional[str] = None  # ISO 8601格式
    end_time: Optional[str] = None
    location: Optional[str] = None
    level: ActionLevel = ActionLevel.L0  # 查询类


# ==================== Knowledge Domain ====================
class KnowledgeAction(BaseModel):
    """知识查询动作（仅文本，不包含视频/音频）"""
    action: Literal["query_manual"]  # v1只支持手册查询
    query: str
    model_filter: Optional[str] = None  # 车型过滤
    version_filter: Optional[str] = None  # 版本过滤
    level: ActionLevel = ActionLevel.L0


# ==================== Chitchat Domain ====================
class ChitchatAction(BaseModel):
    """闲聊动作（无schema约束，无状态）"""
    response: str  # 直接返回的闲聊文本
    level: ActionLevel = ActionLevel.L0


# ==================== Step ====================
class Step(BaseModel):
    """任务步骤"""
    step_id: str
    domain: DomainType
    action: Union[
        VehicleAction,
        NavigationAction,
        MediaAction,
        CalendarAction,
        KnowledgeAction,
        ChitchatAction
    ]
    depends_on: List[str] = Field(default_factory=list)  # 依赖的 step_id 列表
    description: Optional[str] = None  # 人类可读描述
    

# ==================== Task ====================
class Task(BaseModel):
    """任务（一个分支）"""
    task_id: str
    branch_id: str = "main"  # 默认主分支
    steps: List[Step]
    user_intent: Optional[str] = None  # 原始用户意图


# ==================== TaskGraph ====================
class TaskGraph(BaseModel):
    """任务图（可包含多个任务/分支）"""
    tasks: List[Task]
    session_id: str
    timestamp: str  # ISO 8601
    metadata: Optional[Dict[str, Any]] = None
