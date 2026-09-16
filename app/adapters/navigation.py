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
    """导航适配器
    
    P2增强：
    - 从P2 memory读取家/公司地址（content格式："家地址：xxx"）
    - 当前resolve时用地址文本geocode为POI
    - 永不回写坐标到memory
    """
    
    def __init__(self, p2_memory_service=None):
        """
        Args:
            p2_memory_service: P2记忆服务（可选，用于解析家/公司）
        """
        self.p2_memory_service = p2_memory_service
    
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
    
    async def resolve_poi(
        self, 
        poi_name: str, 
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """解析POI名称到坐标
        
        P2增强：
        - 如果poi_name是"家"或"公司"，先从P2 memory读取地址
        - 用地址文本geocode（这里用mock）
        - 失败时返回None（触发追问）
        
        Args:
            poi_name: POI名称
            user_id: 用户ID（用于查询memory）
        
        Returns:
            POI数据（含坐标）或None
        """
        # P2: 尝试从记忆解析家/公司
        if poi_name in ["家", "回家", "家里"] and user_id and self.p2_memory_service:
            addresses = self.p2_memory_service.parse_home_company_address(user_id)
            home_addr = addresses.get('home')
            
            if home_addr:
                print(f"[NavAdapter] Resolved home from P2 memory: {home_addr}")
                # Geocode地址（mock实现：用内置家坐标）
                poi_data = await self._geocode_address(home_addr, poi_name="家")
                if poi_data:
                    return poi_data
                else:
                    # Geocode失败，返回None触发追问
                    print(f"[NavAdapter] Geocode failed for home: {home_addr}")
                    return None
        
        if poi_name in ["公司", "去公司", "单位"] and user_id and self.p2_memory_service:
            addresses = self.p2_memory_service.parse_home_company_address(user_id)
            company_addr = addresses.get('company')
            
            if company_addr:
                print(f"[NavAdapter] Resolved company from P2 memory: {company_addr}")
                poi_data = await self._geocode_address(company_addr, poi_name="公司")
                if poi_data:
                    return poi_data
                else:
                    print(f"[NavAdapter] Geocode failed for company: {company_addr}")
                    return None
        
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
    
    async def _geocode_address(
        self, 
        address: str, 
        poi_name: str
    ) -> Optional[Dict[str, Any]]:
        """地址文本转坐标（mock实现）
        
        实际应调用地图服务API（如高德、百度地图）
        
        Args:
            address: 地址文本
            poi_name: POI名称（用于fallback）
        
        Returns:
            POI数据或None
        """
        # Mock实现：如果地址包含关键词，返回mock坐标
        # 实际应调用 geocoding API
        
        # 这里简化：直接用MOCK_POI_DATABASE的fallback
        if poi_name in MOCK_POI_DATABASE:
            poi_data = MOCK_POI_DATABASE[poi_name]
            return {
                "poi_name": poi_name,
                "latitude": poi_data["lat"],
                "longitude": poi_data["lon"],
                "address": address,  # 使用真实地址文本
            }
        
        # 无法geocode
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
