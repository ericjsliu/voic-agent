# -*- coding: utf-8 -*-
"""Refusal Rules: Zone/Low-Confidence/Side-Chat → No Vehicle Execute"""

from typing import Optional, Dict, Any, List
from ..schemas.taskgraph import TaskGraph, DomainType, ActionLevel


class RefusalRules:
    """拒绝规则：不满足条件时拒绝执行车辆动作"""
    
    # 禁止区域（示例：学校区域、医院区域）
    RESTRICTED_ZONES = [
        {"name": "学校区域", "lat": 39.9042, "lon": 116.4074, "radius_km": 0.5},
        {"name": "医院区域", "lat": 39.9100, "lon": 116.4200, "radius_km": 0.3}
    ]
    
    # 低置信度阈值
    LOW_CONFIDENCE_THRESHOLD = 0.6
    
    # 侧聊关键词（非车辆控制意图）
    SIDE_CHAT_KEYWORDS = [
        "今天天气", "你好", "谢谢", "再见", "你是谁", "聊天",
        "讲个笑话", "唱首歌", "天气怎么样", "心情", "累了"
    ]
    
    @classmethod
    def check_zone_restriction(
        cls,
        taskgraph: TaskGraph,
        current_location: Optional[Dict[str, float]]
    ) -> Optional[str]:
        """检查区域限制
        
        Returns:
            拒绝原因，或None（允许）
        """
        if not current_location:
            return None
        
        # 检查是否有车辆控制动作
        has_vehicle_action = False
        for task in taskgraph.tasks:
            for step in task.steps:
                if step.domain == DomainType.VEHICLE:
                    has_vehicle_action = True
                    break
        
        if not has_vehicle_action:
            return None
        
        # 检查是否在禁止区域
        lat = current_location.get("lat") or current_location.get("latitude")
        lon = current_location.get("lon") or current_location.get("longitude")
        
        if not lat or not lon:
            return None
        
        for zone in cls.RESTRICTED_ZONES:
            distance = cls._calculate_distance(
                lat, lon,
                zone["lat"], zone["lon"]
            )
            if distance <= zone["radius_km"]:
                return f"当前位置在{zone['name']}，禁止车辆控制操作"
        
        return None
    
    @classmethod
    def check_low_confidence(
        cls,
        confidence_score: Optional[float]
    ) -> Optional[str]:
        """检查置信度
        
        Returns:
            拒绝原因，或None（允许）
        """
        if confidence_score is None:
            return None
        
        if confidence_score < cls.LOW_CONFIDENCE_THRESHOLD:
            return f"语音识别置信度过低 ({confidence_score:.2f} < {cls.LOW_CONFIDENCE_THRESHOLD})"
        
        return None
    
    @classmethod
    def check_side_chat(
        cls,
        utterance: str,
        taskgraph: TaskGraph
    ) -> Optional[str]:
        """检查侧聊（非车辆控制意图）
        
        Returns:
            拒绝原因，或None（允许）
        """
        utterance_lower = utterance.lower()
        
        # 如果是侧聊关键词，且TaskGraph包含车辆动作，拒绝
        is_side_chat = any(kw in utterance_lower for kw in cls.SIDE_CHAT_KEYWORDS)
        
        if is_side_chat:
            has_vehicle_action = False
            for task in taskgraph.tasks:
                for step in task.steps:
                    if step.domain == DomainType.VEHICLE:
                        has_vehicle_action = True
                        break
            
            if has_vehicle_action:
                return "识别为侧聊意图，不应包含车辆控制动作"
        
        return None
    
    @classmethod
    def apply_refusal_rules(
        cls,
        taskgraph: TaskGraph,
        utterance: str,
        current_location: Optional[Dict[str, float]] = None,
        confidence_score: Optional[float] = None
    ) -> Optional[str]:
        """应用所有拒绝规则
        
        Returns:
            拒绝原因，或None（允许执行）
        """
        # 规则1: 区域限制
        zone_refusal = cls.check_zone_restriction(taskgraph, current_location)
        if zone_refusal:
            return zone_refusal
        
        # 规则2: 低置信度
        confidence_refusal = cls.check_low_confidence(confidence_score)
        if confidence_refusal:
            return confidence_refusal
        
        # 规则3: 侧聊检测
        side_chat_refusal = cls.check_side_chat(utterance, taskgraph)
        if side_chat_refusal:
            return side_chat_refusal
        
        return None
    
    @staticmethod
    def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点距离（km）- 简化版"""
        from math import radians, cos, sin, asin, sqrt
        
        # Haversine公式
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371  # 地球半径（km）
        return c * r
