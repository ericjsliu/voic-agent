# -*- coding: utf-8 -*-
"""Media domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, MediaAction


class MediaAdapter(BaseDomainAdapter):
    """媒体控制适配器"""
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证媒体控制步骤"""
        action: MediaAction = step.action
        
        # 检查音量范围
        if action.action == "set_volume" and action.volume is not None:
            if not (0 <= action.volume <= 100):
                return False, f"Volume {action.volume} out of range [0, 100]"
        
        # 播放音乐需要有查询词或歌曲名
        if action.action in ["play_music", "search_music"]:
            if not action.query:
                return False, f"Action {action.action} requires query"
        
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行媒体控制"""
        action: MediaAction = step.action
        
        result = {
            "step_id": step.step_id,
            "domain": "media",
            "action": action.action,
            "status": "pending"
        }
        
        if action.query:
            result["query"] = action.query
        if action.volume is not None:
            result["volume"] = action.volume
        if action.station:
            result["station"] = action.station
        
        return result
