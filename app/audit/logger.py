# -*- coding: utf-8 -*-
"""Audit Logger for Full-Chain Tracing

Persists audit events to PostgreSQL if available, else structured logs.
"""

import json
from typing import Optional, List
from datetime import datetime
from .events import AuditEvent, AuditEventType


class AuditLogger:
    """审计日志记录器"""
    
    def __init__(self, pg_store=None):
        self.pg_store = pg_store
        self.events_cache = []  # In-memory cache for recent events (for queries)
        self.max_cache_size = 10000
    
    def emit(self, event: AuditEvent):
        """发出审计事件"""
        # Add to in-memory cache
        self.events_cache.append(event)
        if len(self.events_cache) > self.max_cache_size:
            self.events_cache.pop(0)
        
        # Persist to PostgreSQL if available
        if self.pg_store:
            try:
                self.pg_store.log_audit_event(event)
            except Exception as e:
                print(f"[AuditLogger] Failed to persist to PG: {e}")
        
        # Always log to structured logs
        event_dict = event.model_dump(exclude_none=True)
        print(f"[AUDIT] {json.dumps(event_dict, ensure_ascii=False)}")
    
    def get_by_trace_id(self, trace_id: str) -> List[AuditEvent]:
        """按trace_id查询审计事件（优先从PostgreSQL查询）"""
        if self.pg_store:
            try:
                return self.pg_store.get_audit_events_by_trace(trace_id)
            except Exception as e:
                print(f"[AuditLogger] Failed to query PG: {e}")
        
        # Fallback to in-memory cache
        return [e for e in self.events_cache if e.trace_id == trace_id]
    
    def create_event(
        self,
        trace_id: str,
        session_id: str,
        event_type: AuditEventType,
        **kwargs
    ) -> AuditEvent:
        """创建并发出审计事件"""
        event = AuditEvent(
            trace_id=trace_id,
            session_id=session_id,
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat() + "Z",
            **kwargs
        )
        self.emit(event)
        return event


# Global singleton
_audit_logger: Optional[AuditLogger] = None


def init_audit_logger(pg_store=None):
    """初始化全局审计日志记录器"""
    global _audit_logger
    _audit_logger = AuditLogger(pg_store=pg_store)


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志记录器"""
    if _audit_logger is None:
        init_audit_logger()
    return _audit_logger
