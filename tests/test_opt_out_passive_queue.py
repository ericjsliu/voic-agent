# -*- coding: utf-8 -*-
"""阶段5闸门：opt_out 关记忆 + 被动队列持久化/重试。"""

import os
import tempfile
import time
from unittest.mock import Mock

from app.memory.passive_queue import MemoryControlStore, PassiveQueueWorker
from app.memory.p2_memory_service import P2MemoryService


def test_opt_out_blocks_put_and_search(tmp_path):
    db = tmp_path / "ctrl.db"
    store = MemoryControlStore(db_path=str(db))
    service = P2MemoryService(enable_vector=False, control_store=store, start_worker=False)
    user_id = "account_test:opt_out_driver"

    assert service.opt_out(user_id) is True
    assert service.is_opted_out(user_id) is True

    mid = service.put_memory(
        user_id=user_id,
        content="喜欢听周杰伦",
        source_ref="t",
    )
    assert mid is None

    memories = service.search_memories(user_id=user_id, query="周杰伦")
    assert memories == []

    # 重新开启后可写（无 PG 时 put 可能因 DB 失败返回 None，但不应被 opt_out 拦截）
    assert service.opt_in(user_id) is True
    assert service.is_opted_out(user_id) is False


def test_opt_out_skips_enqueue(tmp_path):
    db = tmp_path / "ctrl.db"
    store = MemoryControlStore(db_path=str(db))
    service = P2MemoryService(enable_vector=False, control_store=store, start_worker=False)
    user_id = "account_test:opt_out_enq"
    service.opt_out(user_id)

    job_id = service.enqueue_passive_extraction(
        user_id=user_id,
        utterance="我平时喜欢听周杰伦",
        assistant_response="好的",
        context={},
        trace_id="t1",
    )
    assert job_id is None
    assert store.pending_count() == 0


def test_passive_queue_survives_restart(tmp_path):
    """入队后不消费；新 store 实例仍能读到 pending（模拟进程重启）。"""
    db = tmp_path / "queue.db"
    store1 = MemoryControlStore(db_path=str(db))
    job_id = store1.enqueue({
        "user_id": "u1",
        "utterance": "我平时喜欢听周杰伦",
        "assistant_response": "好",
        "context": {},
        "trace_id": "t",
    })
    assert job_id
    assert store1.pending_count() == 1

    # 新进程：新 store 指向同一文件
    store2 = MemoryControlStore(db_path=str(db))
    assert store2.pending_count() == 1
    job = store2.claim_next()
    assert job is not None
    assert job["job_id"] == job_id
    assert "周杰伦" in job["payload"]["utterance"]
    store2.ack(job_id)
    assert store2.pending_count() == 0


def test_passive_queue_retry_then_dead(tmp_path):
    db = tmp_path / "queue.db"
    store = MemoryControlStore(db_path=str(db), max_attempts=2)
    job_id = store.enqueue({"user_id": "u1", "utterance": "x"})

    # 第一次失败 → 回 pending
    job = store.claim_next()
    assert job["job_id"] == job_id
    assert store.fail(job_id, "boom1") == "pending"

    # 第二次失败 → 死信
    job = store.claim_next()
    assert job["attempts"] == 2
    assert store.fail(job_id, "boom2") == "dead"
    assert store.dead_count() == 1
    assert store.claim_next() is None


def test_worker_processes_enqueued_job(tmp_path):
    db = tmp_path / "queue.db"
    store = MemoryControlStore(db_path=str(db))
    handled = []

    def handler(payload):
        handled.append(payload)

    worker = PassiveQueueWorker(store, handler, poll_interval=0.05)
    worker.start()
    try:
        store.enqueue({"user_id": "u1", "utterance": "喜欢听周杰伦"})
        for _ in range(40):
            if handled:
                break
            time.sleep(0.05)
        assert len(handled) == 1
        assert "周杰伦" in handled[0]["utterance"]
    finally:
        worker.stop()


def test_processing_recovered_on_worker_start(tmp_path):
    db = tmp_path / "queue.db"
    store = MemoryControlStore(db_path=str(db))
    job_id = store.enqueue({"user_id": "u1", "utterance": "x"})
    # 模拟进程在 processing 时崩溃
    job = store.claim_next()
    assert job["job_id"] == job_id

    recovered = []
    worker = PassiveQueueWorker(store, lambda p: recovered.append(p), poll_interval=0.05)
    worker.start()  # 应把 processing 拉回 pending 再消费
    try:
        for _ in range(40):
            if recovered:
                break
            time.sleep(0.05)
        assert len(recovered) == 1
    finally:
        worker.stop()
