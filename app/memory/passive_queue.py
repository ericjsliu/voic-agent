# -*- coding: utf-8 -*-
<<<<<<< HEAD
"""被动记忆写入持久化队列（SQLite）

BackgroundTasks 进程挂了会丢任务；本队列落盘 + 重试 + 死信，重启后可继续消费。
同时承载账号级 opt_out 开关（关记忆后不写不召回）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Optional


DEFAULT_DB_PATH = os.getenv(
    "P2_MEMORY_CONTROL_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "p2_memory_control.db"),
)


class MemoryControlStore:
    """opt_out 开关 + 被动任务队列（同一 SQLite 文件，进程重启不丢）。"""

    def __init__(self, db_path: Optional[str] = None, max_attempts: int = 5):
        self.db_path = os.path.abspath(db_path or DEFAULT_DB_PATH)
        self.max_attempts = max_attempts
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memory_opt_out (
                        user_id TEXT PRIMARY KEY,
                        opted_out_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS passive_jobs (
                        job_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_passive_jobs_status
                        ON passive_jobs(status, created_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ---------- opt_out ----------

    def set_opt_out(self, user_id: str, opted_out: bool = True) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                if opted_out:
                    now = datetime.utcnow().isoformat() + "Z"
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_opt_out(user_id, opted_out_at) VALUES (?, ?)",
                        (user_id, now),
                    )
                else:
                    conn.execute(
                        "DELETE FROM memory_opt_out WHERE user_id = ?",
                        (user_id,),
                    )
                conn.commit()
                return True
            finally:
                conn.close()

    def is_opted_out(self, user_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM memory_opt_out WHERE user_id = ? LIMIT 1",
                    (user_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    # ---------- passive queue ----------

    def enqueue(self, payload: Dict[str, Any]) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO passive_jobs(job_id, payload, status, attempts, created_at, updated_at)
                    VALUES (?, ?, 'pending', 0, ?, ?)
                    """,
                    (job_id, json.dumps(payload, ensure_ascii=False), now, now),
                )
                conn.commit()
                return job_id
            finally:
                conn.close()

    def claim_next(self) -> Optional[Dict[str, Any]]:
        """原子领取一条 pending 任务，标记为 processing。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT job_id, payload, attempts FROM passive_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                now = datetime.utcnow().isoformat() + "Z"
                conn.execute(
                    """
                    UPDATE passive_jobs
                    SET status = 'processing', updated_at = ?, attempts = attempts + 1
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (now, row["job_id"]),
                )
                if conn.total_changes == 0:
                    conn.commit()
                    return None
                conn.commit()
                return {
                    "job_id": row["job_id"],
                    "payload": json.loads(row["payload"]),
                    "attempts": int(row["attempts"]) + 1,
                }
            finally:
                conn.close()

    def ack(self, job_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                now = datetime.utcnow().isoformat() + "Z"
                conn.execute(
                    "UPDATE passive_jobs SET status = 'done', updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def fail(self, job_id: str, error: str) -> str:
        """失败重试；超过 max_attempts 进死信。返回新状态。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT attempts FROM passive_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                attempts = int(row["attempts"]) if row else self.max_attempts
                now = datetime.utcnow().isoformat() + "Z"
                if attempts >= self.max_attempts:
                    status = "dead"
                else:
                    status = "pending"  # 重新入队等待重试
                conn.execute(
                    """
                    UPDATE passive_jobs
                    SET status = ?, last_error = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (status, (error or "")[:500], now, job_id),
                )
                conn.commit()
                return status
            finally:
                conn.close()

    def pending_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM passive_jobs WHERE status IN ('pending', 'processing')"
                ).fetchone()
                return int(row["c"]) if row else 0
            finally:
                conn.close()

    def dead_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM passive_jobs WHERE status = 'dead'"
                ).fetchone()
                return int(row["c"]) if row else 0
            finally:
                conn.close()

    def reset_processing_to_pending(self) -> int:
        """进程重启：把中断的 processing 拉回 pending。"""
        with self._lock:
            conn = self._connect()
            try:
                now = datetime.utcnow().isoformat() + "Z"
                cur = conn.execute(
                    """
                    UPDATE passive_jobs
                    SET status = 'pending', updated_at = ?
                    WHERE status = 'processing'
                    """,
                    (now,),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()


class PassiveQueueWorker:
    """后台消费被动写入队列。"""

    def __init__(
        self,
        store: MemoryControlStore,
        handler: Callable[[Dict[str, Any]], None],
        poll_interval: float = 0.5,
    ):
        self.store = store
        self.handler = handler
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        recovered = self.store.reset_processing_to_pending()
        if recovered:
            print(f"[PassiveQueue] Recovered {recovered} interrupted jobs")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="passive-memory-worker", daemon=True)
        self._thread.start()
        print("[PassiveQueue] Worker started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            print("[PassiveQueue] Worker stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next()
            if not job:
                time.sleep(self.poll_interval)
                continue
            job_id = job["job_id"]
            try:
                self.handler(job["payload"])
                self.store.ack(job_id)
            except Exception as e:
                status = self.store.fail(job_id, str(e))
                print(f"[PassiveQueue] Job {job_id} failed -> {status}: {e}")
=======
"""被动记忆候选队列（短TTL Redis缓冲）

PRD v1.27: 对话中只入队hot candidate，不立即写入长期记忆
触发durable extraction时机：
- Task terminal state (success/fail/cancel)
- Session idle timeout
- Scheduled consumer tick
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
    """被动记忆消费者（触发durable extraction）
    
    触发时机：
    1. Task terminal state (success/fail/cancel)
    2. Session idle timeout
    3. Scheduled consumer tick (可选，由外部定时任务触发)
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
        trigger_reason: str = "unknown"
    ) -> Dict[str, Any]:
        """消费用户的候选队列，执行durable extraction
        
        Args:
            user_id: 用户ID
            trigger_reason: 触发原因（task_end/idle_timeout/scheduled_tick）
        
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
>>>>>>> 4120886 (实现PRD v1.27被动记忆触发cadence变更)
