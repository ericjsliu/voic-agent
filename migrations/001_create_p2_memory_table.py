#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库迁移：创建P2长期记忆表

执行方式：
  python migrations/001_create_p2_memory_table.py

说明：
  创建long_term_memory_p2表（标准9字段）+ pgvector索引
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from app.storage.database import init_db, get_db, _engine


def create_p2_memory_table():
    """创建P2长期记忆表"""
    print("[Migration] Creating long_term_memory_p2 table...")
    
    # 确保pgvector扩展存在
    with _engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
        print("[Migration] Ensured pgvector extension exists")
    
    # 创建表（通过SQLAlchemy模型）
    from app.storage.models import Base, LongTermMemoryP2
    Base.metadata.create_all(bind=_engine, tables=[LongTermMemoryP2.__table__])
    
    print("[Migration] Created long_term_memory_p2 table")
    
    # 创建额外的向量索引（如果需要）
    with _engine.connect() as conn:
        # pgvector的ivfflat索引（可选，数据量大时有用）
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_p2_memory_embedding_ivfflat
                ON long_term_memory_p2
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """))
            conn.commit()
            print("[Migration] Created vector index (ivfflat)")
        except Exception as e:
            print(f"[Migration] Vector index creation skipped (may need more data): {e}")
    
    print("[Migration] Migration complete!")


def verify_table():
    """验证表创建成功"""
    print("[Migration] Verifying table...")
    
    db = get_db()
    try:
        result = db.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'long_term_memory_p2'
            ORDER BY ordinal_position
        """))
        
        columns = list(result)
        if columns:
            print(f"[Migration] Table has {len(columns)} columns:")
            for col in columns:
                print(f"  - {col.column_name}: {col.data_type}")
        else:
            print("[Migration] WARNING: Table not found or no columns!")
            return False
        
        return True
    finally:
        db.close()


def main():
    """主入口"""
    print("=" * 60)
    print("P2长期记忆表迁移脚本")
    print("=" * 60)
    
    # 初始化数据库连接
    init_db()
    
    # 创建表
    create_p2_memory_table()
    
    # 验证
    if verify_table():
        print("\n✓ Migration successful!")
    else:
        print("\n✗ Migration verification failed!")
        sys.exit(1)


if __name__ == '__main__':
    main()
