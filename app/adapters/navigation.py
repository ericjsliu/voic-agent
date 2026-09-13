# -*- coding: utf-8 -*-
"""Navigation domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, NavigationAction


# Mock地图数据：POI名称 -> 坐标
MOCK_POI_DATABASE = {
    "家": {"lat": 39.9042, "lon": 116.4074, "address": "北京市东城区"},
    "公司": {"lat": 39.9163, "lon": 116.3971, "address": "北京市西城区"},
    "超市": {"lat": 39.9100, "lon": 116.4000, "address": "北京市朝阳区"},
    "机场": {"lat": 40.0799, "lon": 116.6031, "address": "北京首都国际机场"},
    "医院": {"lat": 39.9050, "lon": 116.4200, "address": "北京协和医院"},
    "公园": {"lat": 39.8820, "lon": 116.4070, "address": "天坛公园"},
}


class NavigationAdapter(BaseDomainAdapter):
    """导航适配器"""
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证导航步骤"""
        action: NavigationAction = step.action
        
        if action.action == "nav_to":
            if not action.goal:
                return False, "nav_to requires goal"
            
            # 检查POI是否已解析
            if not action.goal.poi_name:
                return False, "POI name not resolved"
            
            # 检查坐标
            if action.goal.latitude == 0 or action.goal.longitude == 0:
                return False, "Invalid coordinates"
        
        return True, None
    
    async def resolve_poi(self, poi_name: str) -> Optional[Dict[str, Any]]:
        """解析POI名称到坐标（使用mock地图）"""
        # 在真实系统中，这里会调用地图服务API
        if poi_name in MOCK_POI_DATABASE:
            poi_data = MOCK_POI_DATABASE[poi_name]
            return {
                "poi_name": poi_name,
                "latitude": poi_data["lat"],
                "longitude": poi_data["lon"],
                "address": poi_data.get("address"),
            }
        
        # 尝试模糊匹配
        for key in MOCK_POI_DATABASE:
            if poi_name in key or key in poi_name:
                poi_data = MOCK_POI_DATABASE[key]
                return {
                    "poi_name": key,
                    "latitude": poi_data["lat"],
                    "longitude": poi_data["lon"],
                    "address": poi_data.get("address"),
                }
        
        return None
    
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行导航"""
        action: NavigationAction = step.action
        
        result = {
            "step_id": step.step_id,
            "domain": "navigation",
            "action": action.action,
            "status": "pending"
        }
        
        if action.action == "nav_to" and action.goal:
            result.update({
                "goal": {
                    "poi_name": action.goal.poi_name,
                    "latitude": action.goal.latitude,
                    "longitude": action.goal.longitude,
                    "address": action.goal.address,
                },
                "route_prefs": {
                    "avoid_highway": action.route_prefs.avoid_highway,
                    "avoid_toll": action.route_prefs.avoid_toll,
                    "avoid_ferry": action.route_prefs.avoid_ferry,
                    "strategy": action.route_prefs.strategy.value,
                    "via_points": action.route_prefs.via_points,
                }
            })
        
        return result
