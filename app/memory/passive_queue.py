# -*- coding: utf-8 -*-
"""被动记忆候选队列（短TTL Redis缓冲）

PRD v1.27修正: 对话中只入队hot candidate，不立即写入长期记忆
被动提取仅通过scheduled batch job触发（nightly/每N小时）：
- Batch job扫描PG task/audit records
- 可选参考Redis hot candidates队列
- Score≥0.7 → 10-class → Memory.put

Redis队列作用：
- 短TTL缓冲，供batch job参考
- Task terminal state持久化到PG（非Memory.put）
- Session idle不触发立即写入
"""

import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime


class PassiveMemoryCandidateQueue:
    """被动记忆候选队列（Redis短TTL缓冲）"""
    
    # 候选队列key前缀
    QUEUE_KEY_PREFIX = "passive_memory_queue:"
    
    # 短TTL: 30分钟（避免长期堆积）
    CANDIDATE_TTL = 1800
    
    def __init__(self, redis_client):
        """
        Args:
            redis_client: Redis客户端（redis-py）
        """
        self.redis = redis_client
    
    def enqueue_candidate(
        self,
        user_id: str,
        session_id: str,
        utterance: str,
        assistant_response: str,
        context: Dict[str, Any],
        trace_id: Optional[str] = None
    ) -> bool:
        """入队一个被动记忆候选（对话轮次中调用）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            utterance: 用户话语
            assistant_response: 助手回复
            context: 对话上下文
            trace_id: 追踪ID
        
        Returns:
            是否成功入队
        """
        try:
            # 队列key按user_id分组
            queue_key = f"{self.QUEUE_KEY_PREFIX}{user_id}"
            
            # 候选数据
            candidate = {
                "session_id": session_id,
                "utterance": utterance,
                "assistant_response": assistant_response,
                "context": context,
                "trace_id": trace_id or "unknown",
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # 右推入队列
            self.redis.rpush(queue_key, json.dumps(candidate, ensure_ascii=False))
            
            # 设置过期时间（短TTL：30分钟）
            self.redis.expire(queue_key, self.CANDIDATE_TTL)
            
            print(f"[PassiveQueue] Enqueued candidate for user {user_id[:20]}")
            return True
            
        except Exception as e:
            print(f"[PassiveQueue] Error enqueueing candidate: {e}")
            return False
    
    def get_all_candidates(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户所有候选（用于批量处理）
        
        Args:
            user_id: 用户ID
        
        Returns:
            候选列表
        """
        try:
            queue_key = f"{self.QUEUE_KEY_PREFIX}{user_id}"
            
            # 获取所有候选（不删除）
            raw_items = self.redis.lrange(queue_key, 0, -1)
            
            candidates = []
            for raw in raw_items:
                try:
                    candidate = json.loads(raw)
                    candidates.append(candidate)
                except json.JSONDecodeError as e:
                    print(f"[PassiveQueue] Invalid candidate JSON: {e}")
                    continue
            
            return candidates
            
        except Exception as e:
            print(f"[PassiveQueue] Error getting candidates: {e}")
            return []
    
    def clear_candidates(self, user_id: str) -> int:
        """清空用户候选队列（处理完成后调用）
        
        Args:
            user_id: 用户ID
        
        Returns:
            清空的候选数量
        """
        try:
            queue_key = f"{self.QUEUE_KEY_PREFIX}{user_id}"
            count = self.redis.llen(queue_key)
            self.redis.delete(queue_key)
            print(f"[PassiveQueue] Cleared {count} candidates for user {user_id[:20]}")
            return count
            
        except Exception as e:
            print(f"[PassiveQueue] Error clearing candidates: {e}")
            return 0
    
    def get_queue_length(self, user_id: str) -> int:
        """获取队列长度
        
        Args:
            user_id: 用户ID
        
        Returns:
            队列长度
        """
        try:
            queue_key = f"{self.QUEUE_KEY_PREFIX}{user_id}"
            return self.redis.llen(queue_key)
        except Exception as e:
            print(f"[PassiveQueue] Error getting queue length: {e}")
            return 0


class PassiveMemoryConsumer:
    """被动记忆消费者（仅由scheduled batch job调用）
    
    PRD v1.27修正：
    - 不在Task terminal state触发
    - 不在Session idle触发
    - 仅由scheduled batch job调用（nightly/每N小时）
    - Batch job扫描PG task records + 可选参考Redis队列
    """
    
    def __init__(self, queue: PassiveMemoryCandidateQueue, p2_memory_service):
        """
        Args:
            queue: 候选队列
            p2_memory_service: P2长期记忆服务
        """
        self.queue = queue
        self.p2_memory_service = p2_memory_service
    
    def consume_for_user(
        self,
        user_id: str,
        trigger_reason: str = "scheduled_batch"
    ) -> Dict[str, Any]:
        """消费用户的候选队列，执行durable extraction
        
        PRD v1.27修正：仅由scheduled batch job调用
        
        Args:
            user_id: 用户ID
            trigger_reason: 触发原因（应为scheduled_batch或类似）
        
        Returns:
            消费结果统计
        """
        try:
            # 获取所有候选
            candidates = self.queue.get_all_candidates(user_id)
            
            if not candidates:
                print(f"[PassiveConsumer] No candidates for user {user_id[:20]}, trigger={trigger_reason}")
                return {
                    "user_id": user_id,
                    "trigger": trigger_reason,
                    "candidates_count": 0,
                    "extracted_count": 0,
                    "put_count": 0
                }
            
            print(f"[PassiveConsumer] Consuming {len(candidates)} candidates for user {user_id[:20]}, trigger={trigger_reason}")
            
            # 逐条处理候选
            extracted_count = 0
            put_count = 0
            
            for candidate in candidates:
                utterance = candidate.get("utterance", "")
                assistant_response = candidate.get("assistant_response", "")
                context = candidate.get("context", {})
                trace_id = candidate.get("trace_id")
                
                # 调用P2 Memory Service的passive extraction逻辑
                # （会自动评分 ≥0.7 → 提取 → 10类分类 → Memory.put）
                try:
                    # 使用原有的handle_passive_extraction，它包含评分+提取+put逻辑
                    self.p2_memory_service.handle_passive_extraction(
                        user_id=user_id,
                        utterance=utterance,
                        assistant_response=assistant_response,
                        context=context,
                        trace_id=trace_id
                    )
                    extracted_count += 1
                    # 注意：handle_passive_extraction内部会判断score和put，这里只统计尝试次数
                    # 实际put_count需要p2_memory_service返回，暂时用extracted_count估算
                    put_count = extracted_count  # 简化统计
                    
                except Exception as e:
                    print(f"[PassiveConsumer] Error processing candidate: {e}")
                    continue
            
            # 清空队列
            self.queue.clear_candidates(user_id)
            
            result = {
                "user_id": user_id,
                "trigger": trigger_reason,
                "candidates_count": len(candidates),
                "extracted_count": extracted_count,
                "put_count": put_count
            }
            
            print(f"[PassiveConsumer] Consumed {extracted_count}/{len(candidates)} candidates, trigger={trigger_reason}")
            return result
            
        except Exception as e:
            print(f"[PassiveConsumer] Error consuming candidates: {e}")
            return {
                "user_id": user_id,
                "trigger": trigger_reason,
                "error": str(e)
            }
