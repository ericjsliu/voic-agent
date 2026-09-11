# -*- coding: utf-8 -*-
"""Capability Profile Schema for multi-vehicle support"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class ParamRange(BaseModel):
    """参数范围"""
    min: float
    max: float
    step: Optional[float] = None


class ActionCapability(BaseModel):
    """动作能力定义"""
    action: str
    supported: bool = True
    param_ranges: Optional[Dict[str, ParamRange]] = None
    requires_stationary: bool = False
    requires_park: bool = False
    description: Optional[str] = None


class L1GateOverride(BaseModel):
    """L1门控规则覆盖"""
    action: str
    requires_park: Optional[bool] = None
    requires_stationary: Optional[bool] = None
    custom_validation: Optional[str] = None


class FeatureFlags(BaseModel):
    """功能特性标志"""
    has_sunroof: bool = False
    has_rear_ac: bool = False
    has_seat_heating: bool = False
    has_trunk_power: bool = False
    has_360_camera: bool = False
    multi_zone_ac: bool = False


class CommandMapping(BaseModel):
    """命令映射（TaskGraph action → 车辆特定命令）"""
    taskgraph_action: str
    vehicle_command: str
    params_mapping: Optional[Dict[str, str]] = None


class CapabilityProfile(BaseModel):
    """车辆能力档案"""
    model_id: str
    model_name: str
    hardware_option: Optional[str] = None
    config_hash: Optional[str] = None
    
    # 支持的动作白名单
    supported_actions: List[str]
    
    # 详细动作能力
    action_capabilities: Dict[str, ActionCapability] = Field(default_factory=dict)
    
    # L1门控规则覆盖
    l1_gate_overrides: List[L1GateOverride] = Field(default_factory=list)
    
    # 功能特性
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    
    # 命令映射表
    command_mappings: List[CommandMapping] = Field(default_factory=list)
    
    # 元数据
    version: str = "1.0"
    description: Optional[str] = None
    
    def is_action_supported(self, action: str) -> bool:
        """检查动作是否支持"""
        return action in self.supported_actions
    
    def get_action_capability(self, action: str) -> Optional[ActionCapability]:
        """获取动作能力"""
        return self.action_capabilities.get(action)
    
    def validate_param(self, action: str, param_name: str, value: float) -> bool:
        """验证参数范围"""
        capability = self.get_action_capability(action)
        if not capability or not capability.param_ranges:
            return True
        
        param_range = capability.param_ranges.get(param_name)
        if not param_range:
            return True
        
        return param_range.min <= value <= param_range.max
    
    def get_l1_override(self, action: str) -> Optional[L1GateOverride]:
        """获取L1门控覆盖"""
        for override in self.l1_gate_overrides:
            if override.action == action:
                return override
        return None
    
    def get_command_mapping(self, taskgraph_action: str) -> Optional[CommandMapping]:
        """获取命令映射"""
        for mapping in self.command_mappings:
            if mapping.taskgraph_action == taskgraph_action:
                return mapping
        return None
