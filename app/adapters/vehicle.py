# -*- coding: utf-8 -*-
"""Vehicle domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, VehicleAction, ActionLevel


class VehicleAdapter(BaseDomainAdapter):
    """车辆控制适配器（完整座舱命令集）"""
    
    # 需要档位在P的动作（扩展列表）
    REQUIRES_PARK_GEAR = {
        "door_lock", "door_unlock", "child_lock",
        "trunk_open", "frunk_open", "charge_port_open"
    }
    
    # 需要车辆静止的动作
    REQUIRES_STATIONARY = {"sunroof_open", "sunroof_close", "sunshade_open", "sunshade_close"}
    
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
        
        # 检查参数范围
        if action.action == "set_ac_temp" and action.temperature is not None:
            if not (16 <= action.temperature <= 30):
                return False, f"Temperature {action.temperature} out of range [16, 30]"
        
        if action.action == "set_ac_fan_speed" and action.speed is not None:
            if not (1 <= action.speed <= 7):
                return False, f"Fan speed {action.speed} out of range [1, 7]"
        
        if action.action in ["seat_heat", "seat_vent"] and action.level is not None:
            if not (0 <= action.level <= 3):
                return False, f"Seat level {action.level} out of range [0, 3]"
        
        if action.action == "wiper_speed" and action.speed is not None:
            if not (0 <= action.speed <= 5):
                return False, f"Wiper speed {action.speed} out of range [0, 5]"
        
        if action.action in ["window_open", "sunroof_open"] and action.percent is not None:
            if not (0 <= action.percent <= 100):
                return False, f"Percent {action.percent} out of range [0, 100]"
        
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
