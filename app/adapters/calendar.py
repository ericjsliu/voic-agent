# -*- coding: utf-8 -*-
"""Calendar domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, CalendarAction


class CalendarAdapter(BaseDomainAdapter):
    """日历适配器"""
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证日历步骤"""
        action: CalendarAction = step.action
        
        # 添加事件需要标题和时间
        if action.action == "add_event":
            if not action.event_title:
                return False, "add_event requires event_title"
            if not action.start_time:
                return False, "add_event requires start_time"
        
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行日历操作"""
        action: CalendarAction = step.action
        
        result = {
            "step_id": step.step_id,
            "domain": "calendar",
            "action": action.action,
            "status": "pending"
        }
        
        if action.event_title:
            result["event_title"] = action.event_title
        if action.start_time:
            result["start_time"] = action.start_time
        if action.end_time:
            result["end_time"] = action.end_time
        if action.location:
            result["location"] = action.location
        
        return result
