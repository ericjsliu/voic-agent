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
    """车辆控制动作（完整座舱命令集）"""
    action: Literal[
        # Windows & Sunroof
        "window_open", "window_close",
        "sunroof_open", "sunroof_close",
        "sunshade_open", "sunshade_close",
        # Doors & Locks
        "door_lock", "door_unlock", "child_lock",
        # Storage Compartments
        "trunk_open", "frunk_open", "charge_port_open",
        # Climate Control
        "ac_power", "set_ac_temp", "set_ac_fan_mode", "set_ac_fan_speed", "set_ac_circulation",
        "defrost_front", "defrost_rear",
        # Seats
        "seat_heat", "seat_vent",
        # Steering
        "steering_wheel_heat",
        # Lighting
        "ambient_light", "fog_light", "position_light", "low_beam",
        # Mirrors & Wipers
        "mirror_fold", "wiper_speed"
    ]
    target: Optional[str] = None  # "driver", "passenger", "front_left", "all", etc.
    percent: Optional[int] = None  # For windows, sunroof (0-100)
    temperature: Optional[float] = None  # For AC temp (16-30°C)
    speed: Optional[int] = None  # For fan speed, wiper speed
    fan_mode: Optional[str] = None  # "face", "feet", "both"
    circulation: Optional[str] = None  # "internal", "external"
    level: Optional[int] = None  # For seat heat/vent (0-3)
    state: Optional[str] = None  # "on", "off", "enable", "disable"
    level: ActionLevel = ActionLevel.L1  # Default L1, can be overridden in profile


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
    """导航动作（完整导航命令集）"""
    action: Literal[
        "set_nav_goal", "nav_to",  # nav_to is alias for set_nav_goal
        "cancel_nav", "nav_cancel",  # nav_cancel is alias for cancel_nav
        "add_via", "remove_via",
        "set_route_prefs",
        "query_eta", "query_remaining_distance", "query_next_maneuver"
    ]
    goal: Optional[NavGoal] = None
    via_poi: Optional[str] = None  # POI name for via point
    route_prefs: Optional[RoutePreferences] = None
    level: ActionLevel = ActionLevel.L0  # Navigation queries are L0


# ==================== Media Domain ====================
class MediaAction(BaseModel):
    """媒体控制动作（完整媒体命令集）"""
    action: Literal[
        # Playback Control
        "media_play", "play_music",  # play_music is alias
        "media_pause", "pause",  # pause is alias
        "media_next", "next_track",  # next_track is alias
        "media_prev", "prev_track",  # prev_track is alias
        # Volume Control
        "volume_up", "volume_down", "set_volume", "mute",
        # Content Selection
        "play_by_artist", "play_by_title", "play_playlist",
        "play_radio", "play_favorites", "play_random",
        # Source Control
        "switch_source",
        # Search
        "search_music"
    ]
    query: Optional[str] = None  # Search keyword, song name, artist name
    artist: Optional[str] = None  # Artist name
    title: Optional[str] = None  # Song title
    playlist: Optional[str] = None  # Playlist name
    source: Optional[str] = None  # "bluetooth", "usb", "online", "fm"
    volume: Optional[int] = None  # Volume (0-100)
    station: Optional[str] = None  # Radio station
    level: ActionLevel = ActionLevel.L0  # Media controls are L0


# ==================== Calendar Domain ====================
class CalendarAction(BaseModel):
    """日历动作（完整日程命令集）"""
    action: Literal[
        "create_event", "add_event",  # add_event is alias
        "query_events", "query_schedule",  # query_schedule is alias
        "cancel_event",
        "next_appointment", "today_schedule"
    ]
    event_title: Optional[str] = None
    start_time: Optional[str] = None  # ISO 8601 format
    end_time: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    level: ActionLevel = ActionLevel.L0  # Calendar operations are L0


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
    trace_id: str  # PRD v1.9 / detailed-v2.2: full-chain tracing
    timestamp: str  # ISO 8601
    metadata: Optional[Dict[str, Any]] = None
