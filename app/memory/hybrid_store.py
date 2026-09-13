# -*- coding: utf-8 -*-
"""Hybrid Memory Store: Redis (hot) + PostgreSQL (durable)"""

from typing import Optional, Dict, Any, List
from .store import BaseMemoryStore


class HybridMemoryStore(BaseMemoryStore):
    """混合内存存储：Redis用于热路径，PostgreSQL用于持久化
    
    分工：
    - Redis: 会话信息、影子状态、最近对话、实体缓冲区（短TTL）
    - PostgreSQL: 长期记忆白名单（user_prefs, vehicle_config, frequent_destinations, music_prefs）
    """
    
    # 白名单类别（需持久化到PostgreSQL）
    WHITELIST_CATEGORIES = {
        "user_prefs",
        "vehicle_config",
        "frequent_destinations",
        "music_prefs"
    }
    
    def __init__(self, redis_store: BaseMemoryStore, pg_store):
        self.redis = redis_store
        self.pg_store = pg_store
    
    async def get_json(self, key: str) -> Optional[Dict]:
        """优先从Redis读取，如果是白名单则fallback到PostgreSQL"""
        # 先尝试Redis
        data = await self.redis.get_json(key)
        if data is not None:
            return data
        
        # 如果是白名单数据，从PostgreSQL读取
        if self.pg_store and self._is_whitelist_key(key):
            return self._load_from_pg(key)
        
        return None
    
    async def set_json(self, key: str, value: Dict, expire: Optional[int] = None):
        """写入Redis（热缓存），白名单数据同时写入PostgreSQL"""
        # 写入Redis
        await self.redis.set_json(key, value, expire)
        
        # 如果是白名单数据，同时写入PostgreSQL
        if self.pg_store and self._is_whitelist_key(key):
            self._save_to_pg(key, value)
    
    async def delete(self, key: str):
        """删除（仅Redis，PostgreSQL保留历史）"""
        await self.redis.delete(key)
    
    def _is_whitelist_key(self, key: str) -> bool:
        """判断是否是白名单数据"""
        for category in self.WHITELIST_CATEGORIES:
            if key.startswith(f"{category}:"):
                return True
        return False
    
    def _parse_whitelist_key(self, key: str) -> Optional[tuple]:
        """解析白名单key: category:driver_id:subkey"""
        for category in self.WHITELIST_CATEGORIES:
            if key.startswith(f"{category}:"):
                parts = key.split(":", 2)
                if len(parts) >= 2:
                    driver_id = parts[1]
                    subkey = parts[2] if len(parts) > 2 else "default"
                    return (category, driver_id, subkey)
        return None
    
    def _load_from_pg(self, key: str) -> Optional[Dict]:
        """从PostgreSQL加载白名单数据"""
        parsed = self._parse_whitelist_key(key)
        if not parsed:
            return None
        
        category, driver_id, subkey = parsed
        try:
            value = self.pg_store.get_long_term_memory(
                driver_id=driver_id,
                category=category,
                key=subkey
            )
            return value
        except Exception as e:
            print(f"[HybridMemoryStore] Error loading from PG: {e}")
            return None
    
    def _save_to_pg(self, key: str, value: Dict):
        """保存白名单数据到PostgreSQL"""
        parsed = self._parse_whitelist_key(key)
        if not parsed:
            return
        
        category, driver_id, subkey = parsed
        try:
            self.pg_store.set_long_term_memory(
                session_id="",  # 白名单不依赖session
                driver_id=driver_id,
                category=category,
                key=subkey,
                value=value
            )
            print(f"[HybridMemoryStore] Saved to PostgreSQL: {category}/{driver_id}/{subkey}")
        except Exception as e:
            print(f"[HybridMemoryStore] Error saving to PG: {e}")
    
    async def commit(self):
        """提交操作（no-op for async hybrid store）"""
        pass
