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
    additional_metadata = Column(JSONB)  # Renamed from 'metadata' to avoid SQLAlchemy conflict
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
    additional_metadata = Column(JSONB)  # Renamed from 'metadata' to avoid SQLAlchemy conflict
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_driver_event_time', 'driver_id', 'event_type', 'event_time'),
        Index('idx_driver_time', 'driver_id', 'event_time'),
    )


class AuditEventLog(Base):
    """审计事件日志（full-chain tracing PRD v1.9 / detailed-v2.2）
    
    无对话transcript，仅结构化事件
    """
    __tablename__ = "audit_event_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(255), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    
    # 可选字段
    task_id = Column(String(255), index=True)
    step_id = Column(String(255))
    branch_id = Column(String(100))
    domain = Column(String(50))
    action = Column(String(100))
    status = Column(String(50))
    reason = Column(Text)
    model_name = Column(String(100))
    duration_ms = Column(Integer)
    citations_count = Column(Integer)
    
    # 额外元数据
    additional_metadata = Column(JSONB)  # Renamed from 'metadata' to avoid SQLAlchemy conflict
    
    __table_args__ = (
        Index('idx_trace_timestamp', 'trace_id', 'timestamp'),
        Index('idx_session_timestamp', 'session_id', 'timestamp'),
        Index('idx_event_type_timestamp', 'event_type', 'timestamp'),
    )


class LongTermMemoryP2(Base):
    """长期记忆P2主表（标准9字段，详细技术方案 §11.13.6）
    
    支持：主动记忆 + 被动提取 + 向量召回
    - 10类可写：个人基础/背景/偏好/人物关系/目标计划/任务约定/知识经验/限制禁忌/健康习惯/物品设备
    - 硬黑名单：PII/病历/轨迹/Capability Profile/对话原文
    - 家/公司地址存content，无单独address/poi字段
    - 驾驶员隔离：user_id编码为accountId:driverId
    
    向量维度：1536（对齐DashScope text-embedding-v3，与manual_metadata一致）
    """
    __tablename__ = "long_term_memory_p2"
    
    # 标准9字段
    memory_id = Column(String(255), primary_key=True)  # 全局唯一ID
    user_id = Column(String(255), nullable=False, index=True)  # 账号维度分片键（accountId:driverId）
    content = Column(Text, nullable=False)  # 归一化记忆正文（家/公司以"家地址：xxx"格式存储）
    category = Column(String(50), nullable=False, index=True)  # 10类之一
    embedding = Column(Vector(1536), nullable=True)  # 语义向量（1536维，对齐DashScope text-embedding-v3）
    weight = Column(Float, default=1.0, nullable=False)  # 热度权重
    version_id = Column(Integer, default=1, nullable=False)  # 冲突版本ID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    source_ref = Column(String(500), nullable=True)  # 来源消息引用（脱敏，无原文）
    
    __table_args__ = (
        Index('idx_user_category', 'user_id', 'category'),
        Index('idx_user_updated', 'user_id', 'updated_at'),
    )
