# -*- coding: utf-8 -*-
"""持久化队列用于被动记忆提取（Stage 5）

使用Redis list实现：
- 任务入队：RPUSH
- 任务出队：BLPOP（阻塞）
- 重试机制：失败任务重新入队
- 进程重启后任务不丢失
"""

import json
import time
import threading
from typing import Dict, Any, Optional, Callable
import redis


class DurableMemoryQueue:
    """持久化记忆提取队列（Stage 5）
    
    使用Redis list实现FIFO队列，支持：
    - 持久化存储（进程重启不丢失）
    - Worker池处理（可多进程/多线程）
    - 重试机制（失败任务重新入队）
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "memory:passive_extraction_queue",
        max_retries: int = 3
    ):
        """
        Args:
            redis_url: Redis连接URL
            queue_name: 队列名称
            max_retries: 最大重试次数
        """
        self.redis_client = redis.from_url(redis_url)
        self.queue_name = queue_name
        self.max_retries = max_retries
        self.running = False
        self.worker_thread = None
        
        print(f"[DurableMemoryQueue] Initialized: queue={queue_name}, max_retries={max_retries}")
    
    def enqueue(self, task: Dict[str, Any]) -> bool:
        """入队任务
        
        Args:
            task: 任务数据（dict）
                {
                    'user_id': str,
                    'utterance': str,
                    'assistant_response': str,
                    'context': dict,
                    'trace_id': str,
                    'retry_count': int (optional, default 0)
                }
        
        Returns:
            是否成功入队
        """
        try:
            # 添加时间戳和重试计数
            task['enqueued_at'] = time.time()
            if 'retry_count' not in task:
                task['retry_count'] = 0
            
            # 序列化并push到Redis list
            task_json = json.dumps(task, ensure_ascii=False)
            self.redis_client.rpush(self.queue_name, task_json)
            
            print(f"[DurableMemoryQueue] Enqueued task for user {task.get('user_id')}")
            return True
        
        except Exception as e:
            print(f"[DurableMemoryQueue] Enqueue failed: {e}")
            return False
    
    def start_worker(self, processor: Callable[[Dict[str, Any]], bool]):
        """启动worker线程处理队列任务
        
        Args:
            processor: 任务处理函数，返回True表示成功，False表示失败需重试
        """
        if self.running:
            print(f"[DurableMemoryQueue] Worker already running")
            return
        
        self.running = True
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(processor,),
            daemon=True
        )
        self.worker_thread.start()
        print(f"[DurableMemoryQueue] Worker started")
    
    def stop_worker(self):
        """停止worker线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        print(f"[DurableMemoryQueue] Worker stopped")
    
    def _worker_loop(self, processor: Callable[[Dict[str, Any]], bool]):
        """Worker循环（阻塞式）
        
        Args:
            processor: 任务处理函数
        """
        while self.running:
            try:
                # BLPOP：阻塞式pop（timeout 1秒）
                result = self.redis_client.blpop(self.queue_name, timeout=1)
                
                if result is None:
                    continue  # 超时，继续循环
                
                _, task_json = result
                task = json.loads(task_json)
                
                # 处理任务
                success = processor(task)
                
                if success:
                    print(f"[DurableMemoryQueue] Task processed successfully: {task.get('user_id')}")
                else:
                    # 处理失败：重试
                    retry_count = task.get('retry_count', 0)
                    if retry_count < self.max_retries:
                        task['retry_count'] = retry_count + 1
                        self.enqueue(task)
                        print(f"[DurableMemoryQueue] Task failed, retry {task['retry_count']}/{self.max_retries}: {task.get('user_id')}")
                    else:
                        print(f"[DurableMemoryQueue] Task failed permanently after {self.max_retries} retries: {task.get('user_id')}")
            
            except Exception as e:
                print(f"[DurableMemoryQueue] Worker error: {e}")
                time.sleep(1)  # 避免错误循环
    
    def get_queue_size(self) -> int:
        """获取队列大小"""
        try:
            return self.redis_client.llen(self.queue_name)
        except Exception as e:
            print(f"[DurableMemoryQueue] Get queue size failed: {e}")
            return 0
    
    def clear_queue(self):
        """清空队列（仅用于测试）"""
        try:
            self.redis_client.delete(self.queue_name)
            print(f"[DurableMemoryQueue] Queue cleared")
        except Exception as e:
            print(f"[DurableMemoryQueue] Clear queue failed: {e}")
