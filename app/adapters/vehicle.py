# -*- coding: utf-8 -*-
"""Vehicle domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, VehicleAction, ActionLevel


class VehicleAdapter(BaseDomainAdapter):
    """车辆控制适配器"""
    
    # 需要档位在P的动作
    REQUIRES_PARK_GEAR = {"door_lock", "door_unlock", "trunk_open"}
    
    # 需要车辆静止的动作
    REQUIRES_STATIONARY = {"sunroof_open", "sunroof_close"}
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证车辆控制步骤"""
        action: VehicleAction = step.action
        
        # 检查档位要求
        if action.action in self.REQUIRES_PARK_GEAR:
            gear = shadow_state.get("gear", "P")
            if gear != "P":
                return False, f"Action {action.action} requires gear in P, current: {gear}"
        
        # 检查车速要求
        if action.action in self.REQUIRES_STATIONARY:
            speed = shadow_state.get("speed_kmh", 0)
            if speed > 0:
                return False, f"Action {action.action} requires vehicle stationary, current speed: {speed}"
        
        # 检查温度范围
        if action.action == "ac_set_temp" and action.value is not None:
            if not (16 <= action.value <= 30):
                return False, f"Temperature {action.value} out of range [16, 30]"
        
        # L2级别动作需要特殊标记（由Orchestrator处理确认）
        if action.level == ActionLevel.L2:
            # L2动作本身是有效的，但需要用户确认
            pass
        
        return True, None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行车辆控制
        
        在真实系统中，这里会通过CAN总线或车辆API执行控制。
        Mock模式下，返回执行结果供MQTT下行。
        """
        action: VehicleAction = step.action
        
        result = {
            "step_id": step.step_id,
            "domain": "vehicle",
            "action": action.action,
            "target": action.target,
            "value": action.value,
            "level": action.level.value,
            "status": "pending"  # 等待车辆确认
        }
        
        return result
