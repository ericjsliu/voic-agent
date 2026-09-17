# -*- coding: utf-8 -*-
"""工具调用幂等性管理 - PRD v1.37 Feature 1

每个MQTT下行/适配器执行步骤获得稳定的tool_use_id（UUID或trace_id+step_id的哈希）。
Gateway/Orchestrator记录tool_use_id → result映射；重复下行/重试返回缓存的ACK，不重新执行车辆/媒体/导航副作用。
"""

import hashlib
import json
from typing import Optional, Dict, Any
from datetime import timedelta


class IdempotencyManager:
    """幂等性管理器：缓存tool_use_id → result"""
    
    # 幂等性缓存TTL（秒）- 24小时足够处理重试场景
    CACHE_TTL = 3600 * 24
    
    def __init__(self, redis_client):
        """初始化幂等性管理器
        
        Args:
            redis_client: Redis客户端（redis.Redis实例）
        """
        self.redis = redis_client
    
    def generate_tool_use_id(self, trace_id: str, step_id: str) -> str:
        """生成稳定的tool_use_id
        
        使用trace_id+step_id的SHA256哈希作为tool_use_id，确保：
        - 同一步骤在重试时保持相同的ID
        - 不同步骤有不同的ID
        
        Args:
            trace_id: 追踪ID
            step_id: 步骤ID
            
        Returns:
            tool_use_id (32字符十六进制字符串)
        """
        composite = f"{trace_id}:{step_id}"
        return hashlib.sha256(composite.encode('utf-8')).hexdigest()
    
    def get_cached_result(self, tool_use_id: str) -> Optional[Dict[str, Any]]:
        """获取缓存的执行结果
        
        Args:
            tool_use_id: 工具使用ID
            
        Returns:
            缓存的结果字典，不存在返回None
        """
        try:
            key = f"idempotency:{tool_use_id}"
            cached = self.redis.get(key)
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            print(f"[IdempotencyManager] Error getting cached result: {e}")
            return None
    
    def cache_result(self, tool_use_id: str, result: Dict[str, Any]) -> bool:
        """缓存执行结果
        
        Args:
            tool_use_id: 工具使用ID
            result: 执行结果字典
            
        Returns:
            是否成功缓存
        """
        try:
            key = f"idempotency:{tool_use_id}"
            value = json.dumps(result)
            self.redis.setex(key, self.CACHE_TTL, value)
            return True
        except Exception as e:
            print(f"[IdempotencyManager] Error caching result: {e}")
            return False
    
    def clear_cache(self, tool_use_id: str) -> bool:
        """清除缓存（用于测试或显式取消）
        
        Args:
            tool_use_id: 工具使用ID
            
        Returns:
            是否成功清除
        """
        try:
            key = f"idempotency:{tool_use_id}"
            return bool(self.redis.delete(key))
        except Exception as e:
            print(f"[IdempotencyManager] Error clearing cache: {e}")
            return False
