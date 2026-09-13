# -*- coding: utf-8 -*-
"""Session Entity Buffer (Minute-level Hot Cache)"""

from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import json


class EntityBuffer:
    """会话实体缓冲区（分钟级热数据，Redis存储）
    
    存储：
    - 最近的POI
    - 媒体对象
    - 车辆对象
    - 候选列表
    
    在Context Assemble时注入，驾驶员切换时清空
    """
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.ttl = 300  # 5分钟TTL
    
    def _get_key(self, session_id: str, entity_type: str) -> str:
        """生成Redis key"""
        return f"entity_buffer:{session_id}:{entity_type}"
    
    def set_last_poi(
        self,
        session_id: str,
        poi: Dict[str, Any]
    ):
        """设置最近的POI"""
        key = self._get_key(session_id, "last_poi")
        self.redis.setex(
            key,
            self.ttl,
            json.dumps(poi, ensure_ascii=False)
        )
    
    def get_last_poi(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取最近的POI"""
        key = self._get_key(session_id, "last_poi")
        data = self.redis.get(key)
        return json.loads(data) if data else None
    
    def set_last_media(
        self,
        session_id: str,
        media: Dict[str, Any]
    ):
        """设置最近的媒体对象"""
        key = self._get_key(session_id, "last_media")
        self.redis.setex(
            key,
            self.ttl,
            json.dumps(media, ensure_ascii=False)
        )
    
    def get_last_media(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取最近的媒体对象"""
        key = self._get_key(session_id, "last_media")
        data = self.redis.get(key)
        return json.loads(data) if data else None
    
    def set_candidate_list(
        self,
        session_id: str,
        list_type: str,
        candidates: List[Dict[str, Any]]
    ):
        """设置候选列表（POI列表、歌曲列表等）"""
        key = self._get_key(session_id, f"candidates_{list_type}")
        self.redis.setex(
            key,
            self.ttl,
            json.dumps(candidates, ensure_ascii=False)
        )
    
    def get_candidate_list(
        self,
        session_id: str,
        list_type: str
    ) -> Optional[List[Dict[str, Any]]]:
        """获取候选列表"""
        key = self._get_key(session_id, f"candidates_{list_type}")
        data = self.redis.get(key)
        return json.loads(data) if data else None
    
    def set_vehicle_object(
        self,
        session_id: str,
        vehicle_obj: Dict[str, Any]
    ):
        """设置最近的车辆对象（如最后设置的温度、音量等）"""
        key = self._get_key(session_id, "last_vehicle_obj")
        self.redis.setex(
            key,
            self.ttl,
            json.dumps(vehicle_obj, ensure_ascii=False)
        )
    
    def get_vehicle_object(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """获取最近的车辆对象"""
        key = self._get_key(session_id, "last_vehicle_obj")
        data = self.redis.get(key)
        return json.loads(data) if data else None
    
    def clear_all(
        self,
        session_id: str
    ):
        """清空会话的所有实体缓冲区（驾驶员切换时）"""
        pattern = f"entity_buffer:{session_id}:*"
        keys = self.redis.keys(pattern)
        if keys:
            self.redis.delete(*keys)
            print(f"[EntityBuffer] Cleared {len(keys)} entity buffers for session {session_id}")
    
    def get_all(
        self,
        session_id: str
    ) -> Dict[str, Any]:
        """获取会话的所有实体缓冲区（用于Context Assemble）"""
        return {
            "last_poi": self.get_last_poi(session_id),
            "last_media": self.get_last_media(session_id),
            "last_vehicle_obj": self.get_vehicle_object(session_id),
            "poi_candidates": self.get_candidate_list(session_id, "poi"),
            "media_candidates": self.get_candidate_list(session_id, "media")
        }
