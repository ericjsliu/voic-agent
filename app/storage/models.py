# -*- coding: utf-8 -*-
"""PostgreSQL Models for Persistent Storage"""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON, Boolean, Index
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

from .database import Base


class LongTermMemory(Base):
    """长期记忆白名单存储（用户偏好、车辆配置等）"""
    __tablename__ = "long_term_memory"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    driver_id = Column(String(255), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)  # user_prefs, vehicle_config, frequent_destinations, music_prefs
    key = Column(String(255), nullable=False)
    value = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_driver_category', 'driver_id', 'category'),
    )


class CapabilityProfileVersion(Base):
    """能力档案版本管理"""
    __tablename__ = "capability_profile_versions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    hardware_option = Column(String(100))
    version = Column(String(50), nullable=False)
    profile_data = Column(JSONB, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(255))
    
    __table_args__ = (
        Index('idx_model_active', 'model_id', 'is_active'),
    )


class ManualMetadata(Base):
    """用户手册元数据（与pgvector配合）"""
    __tablename__ = "manual_metadata"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(255), nullable=False, index=True)
    model = Column(String(100), index=True)
    version = Column(String(50), index=True)
    section = Column(String(255))
    page = Column(Integer)
    title = Column(Text)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536))  # OpenAI embedding size
    metadata = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_doc_model_version', 'doc_id', 'model', 'version'),
    )


class TaskAuditLog(Base):
    """TaskGraph执行审计日志"""
    __tablename__ = "task_audit_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    driver_id = Column(String(255), nullable=False, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    branch_id = Column(String(255), nullable=False)
    taskgraph = Column(JSONB, nullable=False)
    status = Column(String(50), nullable=False)  # created, executing, completed, failed, cancelled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    
    __table_args__ = (
        Index('idx_session_created', 'session_id', 'created_at'),
    )


class WritebackLog(Base):
    """Writeback事件日志"""
    __tablename__ = "writeback_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    step_id = Column(String(255), nullable=False)
    branch_id = Column(String(255), nullable=False)
    event = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    reason = Column(Text)
    timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_task_timestamp', 'task_id', 'timestamp'),
    )


class SpatiotemporalEvent(Base):
    """时空结构化事件摘要（非原始对话）"""
    __tablename__ = "spatiotemporal_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, index=True)
    driver_id = Column(String(255), nullable=False, index=True)
    event_type = Column(String(100), nullable=False, index=True)  # navigation, charging, media, poi_visit, etc.
    event_time = Column(DateTime, nullable=False, index=True)
    location_lat = Column(Float)
    location_lon = Column(Float)
    summary = Column(Text, nullable=False)  # 结构化摘要，非原始对话
    entities = Column(JSONB)  # POI、媒体、车辆对象等
    metadata = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_driver_event_time', 'driver_id', 'event_type', 'event_time'),
        Index('idx_driver_time', 'driver_id', 'event_time'),
    )
