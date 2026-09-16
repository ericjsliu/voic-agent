# -*- coding: utf-8 -*-
"""Navigation domain adapter"""

from typing import Dict, Any, Optional
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, NavigationAction


# TODO: Mock POI database only for non-home/company testing
# Production paths must NOT use this for home/company resolution
MOCK_POI_DATABASE = {
    "超市": {"lat": 39.9100, "lon": 116.4000, "address": "北京市朝阳区"},
    "机场": {"lat": 40.0799, "lon": 116.6031, "address": "北京首都国际机场"},
    "医院": {"lat": 39.9050, "lon": 116.4200, "address": "北京协和医院"},
    "公园": {"lat": 39.8820, "lon": 116.4070, "address": "天坛公园"},
}

# 口语别名 → 标准名（地图工具侧，不是 Planner 切词）
POI_ALIASES = {
    "回家": "家",
    "到家": "家",
    "家里": "家",
    "回公司": "公司",
    "去公司": "公司",
    "单位": "公司",
}


def _pack_poi(key: str) -> Dict[str, Any]:
    poi_data = MOCK_POI_DATABASE[key]
    return {
        "poi_name": key,
        "latitude": poi_data["lat"],
        "longitude": poi_data["lon"],
        "address": poi_data.get("address"),
    }


class NavigationAdapter(BaseDomainAdapter):
    """导航适配器
    
    P2架构（云端不做geocode）：
    - 从P2 memory读取家/公司地址（content格式："家地址：xxx"）
    - 返回address_text给车端，车端自行geocode和导航
    - 云端不返回坐标，不调用geocode API
    - 如果memory中没有地址，返回None（让上层询问用户）
    """
    
    def __init__(self, p2_memory_service=None):
        """
        Args:
            p2_memory_service: P2记忆服务（必需，用于解析家/公司）
        """
        self.p2_memory_service = p2_memory_service
    
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证导航步骤"""
        action: NavigationAction = step.action
        
        if action.action in ("nav_to", "set_nav_goal"):
            if not action.goal:
                return False, f"{action.action} requires goal"
            
            # 检查POI是否已解析
            if not action.goal.poi_name:
                return False, "POI name not resolved"
            
            # 家/公司只需要address，其他POI需要坐标
            if action.goal.poi_name in ["家", "公司"]:
                if not action.goal.address:
                    return False, "Home/company address not found in memory"
            else:
                # 其他POI需要坐标（如果需要的话）
                # 对于某些情况，坐标可能为0（由车端解析）
                pass
        
        return True, None
    
    async def resolve_poi(
        self,
        poi_name: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """地图工具：解析目的地名称
        
        云端架构（不做geocode）：
        - 如果是"家"或"公司"：从P2 memory读取地址文本，返回address_text（无坐标）
        - 如果memory中没有地址：返回None（上层应询问用户）
        - 其他POI：使用mock数据（TODO: 生产环境应移除或标记为测试用）
        - **绝不**返回fake home/company坐标（如北京市东城区）
        """
        text = (poi_name or "").strip()
        if not text:
            return None
        
        # 标准化别名（精确匹配）
        if text in POI_ALIASES:
            text = POI_ALIASES[text]
        # 如果不是精确匹配，检查是否包含别名（例如"导航回家"包含"回家"）
        else:
            for alias, standard_name in POI_ALIASES.items():
                if alias in text:
                    text = standard_name
                    break

        # 家/公司：从P2 memory读取地址（云端不做geocode）
        if text in ["家", "公司"]:
            if not user_id or not self.p2_memory_service:
                print(f"[NavAdapter] Cannot resolve {text}: no user_id or memory service")
                return None
            
            addresses = self.p2_memory_service.parse_home_company_address(user_id)
            
            if text == "家":
                home_addr = addresses.get('home')
                if home_addr:
                    print(f"[NavAdapter] Resolved home from P2 memory: {home_addr}")
                    return {
                        "poi_name": "家",
                        "address": home_addr,
                        "latitude": 0.0,  # 云端不返回坐标，车端自行geocode
                        "longitude": 0.0,
                    }
                else:
                    print(f"[NavAdapter] Home address not found in memory for user {user_id}")
                    return None
            
            elif text == "公司":
                company_addr = addresses.get('company')
                if company_addr:
                    print(f"[NavAdapter] Resolved company from P2 memory: {company_addr}")
                    return {
                        "poi_name": "公司",
                        "address": company_addr,
                        "latitude": 0.0,  # 云端不返回坐标，车端自行geocode
                        "longitude": 0.0,
                    }
                else:
                    print(f"[NavAdapter] Company address not found in memory for user {user_id}")
                    return None

        # TODO: 其他POI使用mock数据，生产环境应移除或接入真实地图服务
        if text in MOCK_POI_DATABASE:
            return _pack_poi(text)

        # 检查库内标准名是否在query中
        for key in MOCK_POI_DATABASE:
            if len(key) >= 2 and (key in text or text in key):
                return _pack_poi(key)

        # 未找到POI
        print(f"[NavAdapter] POI not found: {text}")
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
        
        if action.action in ("nav_to", "set_nav_goal") and action.goal:
            # 家/公司：只发送address_text给车端
            if action.goal.poi_name in ["家", "公司"]:
                result.update({
                    "goal": {
                        "type": action.goal.poi_name,  # "家" or "公司"
                        "address_text": action.goal.address,  # 地址文本（车端自行geocode）
                    },
                    "route_prefs": {
                        "avoid_highway": action.route_prefs.avoid_highway,
                        "avoid_toll": action.route_prefs.avoid_toll,
                        "avoid_ferry": action.route_prefs.avoid_ferry,
                        "strategy": action.route_prefs.strategy.value,
                        "via_points": action.route_prefs.via_points,
                    }
                })
            else:
                # 其他POI：发送完整信息
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
