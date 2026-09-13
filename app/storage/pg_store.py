# -*- coding: utf-8 -*-
"""PostgreSQL Store for Persistent Data"""

from datetime import datetime
from typing import Optional, Dict, List, Any
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    LongTermMemory,
    CapabilityProfileVersion,
    TaskAuditLog,
    WritebackLog,
    SpatiotemporalEvent
)


class PostgresStore:
    """PostgreSQL持久化存储（长期记忆、审计日志、事件摘要）"""
    
    def __init__(self):
        pass
    
    # ==================== Long-term Memory ====================
    
    def set_long_term_memory(
        self,
        session_id: str,
        driver_id: str,
        category: str,
        key: str,
        value: Dict[str, Any]
    ):
        """写入长期记忆（白名单）"""
        db: Session = get_db()
        try:
            # 查找或创建
            record = db.query(LongTermMemory).filter_by(
                driver_id=driver_id,
                category=category,
                key=key
            ).first()
            
            if record:
                record.value = value
                record.updated_at = datetime.utcnow()
                record.session_id = session_id
            else:
                record = LongTermMemory(
                    session_id=session_id,
                    driver_id=driver_id,
                    category=category,
                    key=key,
                    value=value
                )
                db.add(record)
            
            db.commit()
            print(f"[PostgresStore] Saved long-term memory: {driver_id}/{category}/{key}")
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error saving long-term memory: {e}")
            raise
        finally:
            db.close()
    
    def get_long_term_memory(
        self,
        driver_id: str,
        category: str,
        key: str
    ) -> Optional[Dict[str, Any]]:
        """读取长期记忆"""
        db: Session = get_db()
        try:
            record = db.query(LongTermMemory).filter_by(
                driver_id=driver_id,
                category=category,
                key=key
            ).first()
            
            return record.value if record else None
        finally:
            db.close()
    
    def get_driver_memories(
        self,
        driver_id: str,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取驾驶员的所有长期记忆"""
        db: Session = get_db()
        try:
            query = db.query(LongTermMemory).filter_by(driver_id=driver_id)
            if category:
                query = query.filter_by(category=category)
            
            records = query.all()
            return [
                {
                    "key": r.key,
                    "value": r.value,
                    "category": r.category,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None
                }
                for r in records
            ]
        finally:
            db.close()
    
    # ==================== Capability Profiles ====================
    
    def save_capability_profile(
        self,
        model_id: str,
        version: str,
        profile_data: Dict[str, Any],
        hardware_option: Optional[str] = None,
        created_by: Optional[str] = None
    ):
        """保存能力档案版本"""
        db: Session = get_db()
        try:
            # 将旧版本标记为非活跃
            db.query(CapabilityProfileVersion).filter_by(
                model_id=model_id,
                hardware_option=hardware_option,
                is_active=True
            ).update({"is_active": False})
            
            # 创建新版本
            profile = CapabilityProfileVersion(
                model_id=model_id,
                hardware_option=hardware_option,
                version=version,
                profile_data=profile_data,
                is_active=True,
                created_by=created_by
            )
            db.add(profile)
            db.commit()
            
            print(f"[PostgresStore] Saved capability profile: {model_id} v{version}")
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error saving capability profile: {e}")
            raise
        finally:
            db.close()
    
    def get_active_capability_profile(
        self,
        model_id: str,
        hardware_option: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """获取活跃的能力档案"""
        db: Session = get_db()
        try:
            query = db.query(CapabilityProfileVersion).filter_by(
                model_id=model_id,
                is_active=True
            )
            if hardware_option:
                query = query.filter_by(hardware_option=hardware_option)
            
            profile = query.first()
            return profile.profile_data if profile else None
        finally:
            db.close()
    
    # ==================== Audit Logs ====================
    
    def log_task_created(
        self,
        session_id: str,
        driver_id: str,
        task_id: str,
        branch_id: str,
        taskgraph: Dict[str, Any]
    ):
        """记录TaskGraph创建"""
        db: Session = get_db()
        try:
            log = TaskAuditLog(
                session_id=session_id,
                driver_id=driver_id,
                task_id=task_id,
                branch_id=branch_id,
                taskgraph=taskgraph,
                status="created"
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error logging task: {e}")
        finally:
            db.close()
    
    def update_task_status(
        self,
        task_id: str,
        status: str,
        error_message: Optional[str] = None
    ):
        """更新TaskGraph状态"""
        db: Session = get_db()
        try:
            log = db.query(TaskAuditLog).filter_by(
                task_id=task_id
            ).order_by(TaskAuditLog.created_at.desc()).first()
            
            if log:
                log.status = status
                if status in ("completed", "failed", "cancelled"):
                    log.completed_at = datetime.utcnow()
                if error_message:
                    log.error_message = error_message
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error updating task status: {e}")
        finally:
            db.close()
    
    def log_writeback(
        self,
        session_id: str,
        task_id: str,
        step_id: str,
        branch_id: str,
        event: str,
        status: str,
        reason: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """记录Writeback事件"""
        db: Session = get_db()
        try:
            log = WritebackLog(
                session_id=session_id,
                task_id=task_id,
                step_id=step_id,
                branch_id=branch_id,
                event=event,
                status=status,
                reason=reason,
                timestamp=timestamp or datetime.utcnow()
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error logging writeback: {e}")
        finally:
            db.close()
    
    # ==================== Spatiotemporal Events ====================
    
    def log_spatiotemporal_event(
        self,
        session_id: str,
        driver_id: str,
        event_type: str,
        event_time: datetime,
        summary: str,
        location_lat: Optional[float] = None,
        location_lon: Optional[float] = None,
        entities: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """记录时空结构化事件（非原始对话）"""
        db: Session = get_db()
        try:
            event = SpatiotemporalEvent(
                session_id=session_id,
                driver_id=driver_id,
                event_type=event_type,
                event_time=event_time,
                location_lat=location_lat,
                location_lon=location_lon,
                summary=summary,
                entities=entities,
                metadata=metadata
            )
            db.add(event)
            db.commit()
            print(f"[PostgresStore] Logged spatiotemporal event: {event_type}")
        except Exception as e:
            db.rollback()
            print(f"[PostgresStore] Error logging event: {e}")
        finally:
            db.close()
    
    def query_spatiotemporal_events(
        self,
        driver_id: str,
        event_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """查询时空事件（如"昨天充电站"）"""
        db: Session = get_db()
        try:
            query = db.query(SpatiotemporalEvent).filter_by(driver_id=driver_id)
            
            if event_type:
                query = query.filter_by(event_type=event_type)
            if start_time:
                query = query.filter(SpatiotemporalEvent.event_time >= start_time)
            if end_time:
                query = query.filter(SpatiotemporalEvent.event_time <= end_time)
            
            events = query.order_by(
                SpatiotemporalEvent.event_time.desc()
            ).limit(limit).all()
            
            return [
                {
                    "event_type": e.event_type,
                    "event_time": e.event_time.isoformat(),
                    "summary": e.summary,
                    "location": {
                        "lat": e.location_lat,
                        "lon": e.location_lon
                    } if e.location_lat and e.location_lon else None,
                    "entities": e.entities,
                    "metadata": e.metadata
                }
                for e in events
            ]
        finally:
            db.close()
