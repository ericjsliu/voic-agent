# -*- coding: utf-8 -*-
"""PostgreSQL Database Setup with pgvector"""

import os
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

# Base for all models
Base = declarative_base()

# Database connection
_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """获取数据库连接URL"""
    return os.getenv(
        "DATABASE_URL",
        "postgresql://cockpit:cockpit@localhost:5432/cockpit_agent"
    )


def init_db():
    """初始化数据库连接"""
    global _engine, _SessionLocal
    
    database_url = get_database_url()
    _engine = create_engine(
        database_url,
        poolclass=NullPool,  # 简化连接池管理
        echo=False
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    
    print(f"[Database] Initialized connection to PostgreSQL")


def create_tables():
    """创建所有数据表"""
    from . import models  # Import to register models
    Base.metadata.create_all(bind=_engine)
    print(f"[Database] Created all tables")


def get_db() -> Session:
    """获取数据库会话"""
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        return db
    except:
        db.close()
        raise


def close_db():
    """关闭数据库连接"""
    global _engine
    if _engine:
        _engine.dispose()
        print(f"[Database] Closed database connection")
