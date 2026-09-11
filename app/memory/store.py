# -*- coding: utf-8 -*-
"""Memory store with Redis + in-memory fallback"""

import json
import os
from typing import Optional, Dict, Any, Set
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# 白名单：允许持久化的键前缀
MEMORY_WHITELIST = {
    "user_prefs:",  # 用户偏好
    "vehicle_config:",  # 车辆配置
    "frequent_destinations:",  # 常用目的地
    "music_prefs:",  # 音乐偏好
}


class BaseMemoryStore(ABC):
    """内存存储基类"""
    
    @abstractmethod
    async def get(self, key: str) -> Optional[str]:
        """获取值"""
        pass
    
    @abstractmethod
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """设置值（两阶段写入，白名单检查）"""
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> bool:
        """删除值"""
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        pass
    
    def _is_whitelisted(self, key: str) -> bool:
        """检查键是否在白名单中"""
        return any(key.startswith(prefix) for prefix in MEMORY_WHITELIST)
    
    async def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        """获取JSON值"""
        value = await self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None
    
    async def set_json(self, key: str, value: Dict[str, Any], expire: Optional[int] = None) -> bool:
        """设置JSON值"""
        return await self.set(key, json.dumps(value), expire)


class RedisMemoryStore(BaseMemoryStore):
    """Redis存储实现"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        if not REDIS_AVAILABLE:
            raise ImportError("redis package not installed")
        self.client = redis.from_url(redis_url, decode_responses=True)
        self._pending_writes: Dict[str, tuple[str, Optional[int]]] = {}
    
    async def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except Exception as e:
            print(f"Redis get error: {e}")
            return None
    
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """两阶段写入：先暂存，commit时批量写入"""
        if not self._is_whitelisted(key):
            print(f"Key {key} not in whitelist, skipping persistent write")
            return False
        
        self._pending_writes[key] = (value, expire)
        return True
    
    async def commit(self) -> bool:
        """提交所有暂存的写入"""
        if not self._pending_writes:
            return True
        
        try:
            pipe = self.client.pipeline()
            for key, (value, expire) in self._pending_writes.items():
                if expire:
                    pipe.setex(key, expire, value)
                else:
                    pipe.set(key, value)
            pipe.execute()
            self._pending_writes.clear()
            return True
        except Exception as e:
            print(f"Redis commit error: {e}")
            self._pending_writes.clear()
            return False
    
    async def delete(self, key: str) -> bool:
        try:
            return bool(self.client.delete(key))
        except Exception as e:
            print(f"Redis delete error: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        try:
            return bool(self.client.exists(key))
        except Exception as e:
            print(f"Redis exists error: {e}")
            return False


class InMemoryStore(BaseMemoryStore):
    """内存存储实现（Redis不可用时的fallback）"""
    
    def __init__(self):
        self._store: Dict[str, tuple[str, Optional[datetime]]] = {}
        self._pending_writes: Dict[str, tuple[str, Optional[int]]] = {}
    
    async def get(self, key: str) -> Optional[str]:
        if key in self._store:
            value, expire_at = self._store[key]
            if expire_at is None or expire_at > datetime.now():
                return value
            else:
                del self._store[key]
        return None
    
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """两阶段写入"""
        if not self._is_whitelisted(key):
            print(f"Key {key} not in whitelist, skipping write")
            return False
        
        self._pending_writes[key] = (value, expire)
        return True
    
    async def commit(self) -> bool:
        """提交所有暂存的写入"""
        for key, (value, expire) in self._pending_writes.items():
            expire_at = None
            if expire:
                expire_at = datetime.now() + timedelta(seconds=expire)
            self._store[key] = (value, expire_at)
        self._pending_writes.clear()
        return True
    
    async def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False
    
    async def exists(self, key: str) -> bool:
        value = await self.get(key)
        return value is not None


# 全局单例
_memory_store: Optional[BaseMemoryStore] = None


def get_memory_store() -> BaseMemoryStore:
    """获取内存存储实例（单例）"""
    global _memory_store
    
    if _memory_store is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        
        if REDIS_AVAILABLE:
            try:
                _memory_store = RedisMemoryStore(redis_url)
                print("Using Redis memory store")
            except Exception as e:
                print(f"Failed to connect to Redis: {e}, falling back to in-memory store")
                _memory_store = InMemoryStore()
        else:
            print("Redis not available, using in-memory store")
            _memory_store = InMemoryStore()
    
    return _memory_store
