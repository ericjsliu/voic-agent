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

