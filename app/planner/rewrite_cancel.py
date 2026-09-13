# -*- coding: utf-8 -*-
"""Rewrite/Cancel Rules for Same task_id"""

from typing import Optional, Dict, Any
from datetime import datetime, timedelta


class RewriteCancelManager:
    """改写/取消管理器（同一task_id的重写和取消逻辑）"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.rewrite_window_seconds = 30  # 30秒内的任务可以被改写
    
    def _get_active_task_key(self, session_id: str) -> str:
        """获取活跃任务的Redis key"""
        return f"active_task:{session_id}"
    
    def record_active_task(
        self,
        session_id: str,
        task_id: str,
        taskgraph: Dict[str, Any]
    ):
        """记录活跃任务"""
        task_data = {
            "task_id": task_id,
            "taskgraph": taskgraph,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.redis.setex(
            self._get_active_task_key(session_id),
            self.rewrite_window_seconds,
            str(task_data)
        )
    
    def get_active_task(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取活跃任务（如果在改写窗口内）"""
        import json
        data = self.redis.get(self._get_active_task_key(session_id))
        if data:
            try:
                return eval(data.decode('utf-8'))
            except:
                return None
        return None
    
    def clear_active_task(self, session_id: str):
        """清除活跃任务"""
        self.redis.delete(self._get_active_task_key(session_id))
    
    def check_rewrite_or_cancel(
        self,
        session_id: str,
        current_utterance: str
    ) -> Optional[Dict[str, Any]]:
        """检查是否是改写或取消指令
        
        Returns:
            {
                "action": "rewrite" | "cancel",
                "original_task_id": "xxx",
                "reason": "user requested rewrite/cancel"
            }
            或 None（不是改写/取消）
        """
        active_task = self.get_active_task(session_id)
        if not active_task:
            return None
        
        utterance_lower = current_utterance.lower()
        
        # 取消关键词
        cancel_keywords = ["取消", "算了", "不要了", "停止"]
        if any(kw in utterance_lower for kw in cancel_keywords):
            return {
                "action": "cancel",
                "original_task_id": active_task["task_id"],
                "reason": "用户取消操作"
            }
        
        # 改写关键词（修改、换成、改成）
        rewrite_keywords = ["改成", "换成", "修改", "不是", "应该是"]
        if any(kw in utterance_lower for kw in rewrite_keywords):
            return {
                "action": "rewrite",
                "original_task_id": active_task["task_id"],
                "reason": "用户改写指令"
            }
        
        return None
