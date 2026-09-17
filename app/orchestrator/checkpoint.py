# -*- coding: utf-8 -*-
"""检查点和恢复管理 - PRD v1.37 Feature 2

持久化TaskGraph + Orchestrator节点进度到Redis（和可选的PostgreSQL）。
在重连/恢复会话时，从最后检查点继续，而非从头重新规划（除非用户取消）。
"""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from pydantic import BaseModel


class CheckpointData(BaseModel):
    """检查点数据结构"""
    session_id: str
    trace_id: str
    taskgraph: Dict[str, Any]  # TaskGraph序列化
    step_states: Dict[str, Dict[str, Any]]  # step_id -> StepState序列化
    completed_steps: List[str]
    failed_steps: List[str]
    shadow_state: Dict[str, Any]
    timestamp: str
    checkpoint_version: str = "1.0"


class CheckpointManager:
    """检查点管理器：保存和恢复orchestrator状态"""
    
    # 检查点TTL（秒）- 1小时，足够处理临时断线重连
    CHECKPOINT_TTL = 3600
    
    def __init__(self, redis_client, pg_store=None):
        """初始化检查点管理器
        
        Args:
            redis_client: Redis客户端
            pg_store: PostgreSQL存储（可选）
        """
        self.redis = redis_client
        self.pg_store = pg_store
    
    def save_checkpoint(
        self,
        session_id: str,
        trace_id: str,
        taskgraph_dict: Dict[str, Any],
        step_states: Dict[str, Any],
        completed_steps: List[str],
        failed_steps: List[str],
        shadow_state: Dict[str, Any]
    ) -> bool:
        """保存检查点
        
        Args:
            session_id: 会话ID
            trace_id: 追踪ID
            taskgraph_dict: TaskGraph字典
            step_states: 步骤状态字典
            completed_steps: 已完成步骤列表
            failed_steps: 失败步骤列表
            shadow_state: 影子状态
            
        Returns:
            是否成功保存
        """
        try:
            checkpoint = CheckpointData(
                session_id=session_id,
                trace_id=trace_id,
                taskgraph=taskgraph_dict,
                step_states=step_states,
                completed_steps=completed_steps,
                failed_steps=failed_steps,
                shadow_state=shadow_state,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
            
            # 保存到Redis（热路径）
            key = f"checkpoint:{session_id}:{trace_id}"
            value = checkpoint.model_dump_json()
            self.redis.setex(key, self.CHECKPOINT_TTL, value)
            
            # 可选：保存到PostgreSQL（持久化）
            if self.pg_store:
                self._save_to_pg(checkpoint)
            
            print(f"[CheckpointManager] Saved checkpoint: {session_id}/{trace_id}")
            return True
            
        except Exception as e:
            print(f"[CheckpointManager] Error saving checkpoint: {e}")
            return False
    
    def load_checkpoint(
        self,
        session_id: str,
        trace_id: Optional[str] = None
    ) -> Optional[CheckpointData]:
        """加载检查点
        
        Args:
            session_id: 会话ID
            trace_id: 追踪ID（可选，不提供则加载最近的）
            
        Returns:
            检查点数据，不存在返回None
        """
        try:
            if trace_id:
                # 加载指定trace_id的检查点
                key = f"checkpoint:{session_id}:{trace_id}"
                cached = self.redis.get(key)
                if cached:
                    return CheckpointData.model_validate_json(cached)
            else:
                # 加载最近的检查点（扫描所有checkpoint:session_id:*）
                pattern = f"checkpoint:{session_id}:*"
                keys = self.redis.keys(pattern)
                if keys:
                    # 按时间戳排序，取最新的
                    checkpoints = []
                    for key in keys:
                        cached = self.redis.get(key)
                        if cached:
                            cp = CheckpointData.model_validate_json(cached)
                            checkpoints.append((cp.timestamp, cp))
                    
                    if checkpoints:
                        checkpoints.sort(key=lambda x: x[0], reverse=True)
                        return checkpoints[0][1]
            
            # Redis未命中，尝试从PostgreSQL加载
            if self.pg_store:
                return self._load_from_pg(session_id, trace_id)
            
            return None
            
        except Exception as e:
            print(f"[CheckpointManager] Error loading checkpoint: {e}")
            return None
    
    def clear_checkpoint(self, session_id: str, trace_id: str) -> bool:
        """清除检查点（任务完成或取消时）
        
        Args:
            session_id: 会话ID
            trace_id: 追踪ID
            
        Returns:
            是否成功清除
        """
        try:
            key = f"checkpoint:{session_id}:{trace_id}"
            deleted = self.redis.delete(key)
            print(f"[CheckpointManager] Cleared checkpoint: {session_id}/{trace_id}")
            return bool(deleted)
        except Exception as e:
            print(f"[CheckpointManager] Error clearing checkpoint: {e}")
            return False
    
    def list_checkpoints(self, session_id: str) -> List[Dict[str, Any]]:
        """列出会话的所有检查点（用于调试）
        
        Args:
            session_id: 会话ID
            
        Returns:
            检查点元数据列表
        """
        try:
            pattern = f"checkpoint:{session_id}:*"
            keys = self.redis.keys(pattern)
            
            checkpoints = []
            for key in keys:
                cached = self.redis.get(key)
                if cached:
                    cp = CheckpointData.model_validate_json(cached)
                    checkpoints.append({
                        "trace_id": cp.trace_id,
                        "timestamp": cp.timestamp,
                        "completed_steps": len(cp.completed_steps),
                        "failed_steps": len(cp.failed_steps)
                    })
            
            return sorted(checkpoints, key=lambda x: x["timestamp"], reverse=True)
            
        except Exception as e:
            print(f"[CheckpointManager] Error listing checkpoints: {e}")
            return []
    
    def _save_to_pg(self, checkpoint: CheckpointData):
        """保存检查点到PostgreSQL（可选持久化）"""
        # TODO: 实现PostgreSQL持久化逻辑
        # 暂时跳过，Redis已足够处理重连场景
        pass
    
    def _load_from_pg(
        self,
        session_id: str,
        trace_id: Optional[str]
    ) -> Optional[CheckpointData]:
        """从PostgreSQL加载检查点（Redis未命中时的备份）"""
        # TODO: 实现PostgreSQL加载逻辑
        return None
