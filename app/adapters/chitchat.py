# -*- coding: utf-8 -*-
"""Chitchat domain adapter - no schema, no state, no memory"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, ChitchatAction


class ChitchatAdapter(BaseDomainAdapter):
    """闲聊适配器（无schema约束，无长期记忆）"""
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """闲聊无需验证"""
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行闲聊（直接返回response文本）"""
        action: ChitchatAction = step.action
        
        result = {
            "step_id": step.step_id,
            "domain": "chitchat",
            "response": action.response,
            "status": "success"
        }
        
        return result
