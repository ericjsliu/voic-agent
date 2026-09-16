#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库迁移：将向量维度从1536迁移到1024

执行方式：
  python migrations/002_migrate_vector_to_1024.py

说明：
  - 用户锁定embedding维度为1024（text-embedding-v3）
  - 将long_term_memory_p2.embedding从vector(1536)改为vector(1024)
  - 将manual_metadata.embedding从vector(1536)改为vector(1024)
  - 删除旧索引，重建新索引
  - 清空现有embedding数据（因为维度不兼容，需要重新生成）
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from app.storage.database import init_db, _engine


def migrate_vector_dimensions():
    """迁移向量维度：1536 -> 1024"""
    print("=" * 60)
    print("向量维度迁移：1536 -> 1024")
    print("=" * 60)
    
    with _engine.connect() as conn:
        # 1. 检查表是否存在
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('long_term_memory_p2', 'manual_metadata')
        """))
        tables = [row.table_name for row in result]
        
        if not tables:
            print("[Migration] No tables to migrate (tables don't exist yet)")
            return
        
        print(f"[Migration] Found tables: {tables}")
        
        # 2. 迁移long_term_memory_p2表
        if 'long_term_memory_p2' in tables:
            print("\n[Migration] Migrating long_term_memory_p2...")
            
            # 检查当前向量维度
            result = conn.execute(text("""
                SELECT atttypmod 
                FROM pg_attribute 
                WHERE attrelid = 'long_term_memory_p2'::regclass 
                AND attname = 'embedding'
            """))
            row = result.fetchone()
            
            if row and row.atttypmod > 0:
                current_dim = row.atttypmod
                print(f"  Current dimension: {current_dim}")
                
                if current_dim == 1024:
                    print("  Already at 1024 dimensions, skipping")
                else:
                    # 删除旧索引
                    try:
                        conn.execute(text("DROP INDEX IF EXISTS idx_p2_memory_embedding_ivfflat"))
                        print("  Dropped old vector index")
                    except Exception as e:
                        print(f"  Warning: Could not drop old index: {e}")
                    
                    # 清空embedding列（维度不兼容，需要重新生成）
                    conn.execute(text("UPDATE long_term_memory_p2 SET embedding = NULL"))
                    print("  Cleared old embeddings (will be regenerated)")
                    
                    # 修改列类型
                    conn.execute(text("ALTER TABLE long_term_memory_p2 ALTER COLUMN embedding TYPE vector(1024)"))
                    print("  Changed column type to vector(1024)")
                    
                    # 重建索引
                    try:
                        conn.execute(text("""
                            CREATE INDEX idx_p2_memory_embedding_ivfflat
                            ON long_term_memory_p2
                            USING ivfflat (embedding vector_cosine_ops)
                            WITH (lists = 100)
                        """))
                        print("  Created new vector index (1024-dim)")
                    except Exception as e:
                        print(f"  Warning: Vector index creation skipped: {e}")
            else:
                print("  No existing embedding column or already NULL type")
        
        # 3. 迁移manual_metadata表
        if 'manual_metadata' in tables:
            print("\n[Migration] Migrating manual_metadata...")
            
            # 检查当前向量维度
            result = conn.execute(text("""
                SELECT atttypmod 
                FROM pg_attribute 
                WHERE attrelid = 'manual_metadata'::regclass 
                AND attname = 'embedding'
            """))
            row = result.fetchone()
            
            if row and row.atttypmod > 0:
                current_dim = row.atttypmod
                print(f"  Current dimension: {current_dim}")
                
                if current_dim == 1024:
                    print("  Already at 1024 dimensions, skipping")
                else:
                    # 清空embedding列
                    conn.execute(text("UPDATE manual_metadata SET embedding = NULL"))
                    print("  Cleared old embeddings (will be regenerated)")
                    
                    # 修改列类型
                    conn.execute(text("ALTER TABLE manual_metadata ALTER COLUMN embedding TYPE vector(1024)"))
                    print("  Changed column type to vector(1024)")
            else:
                print("  No existing embedding column or already NULL type")
        
        conn.commit()
        print("\n[Migration] Migration complete!")
        print("[Migration] Note: All embeddings have been cleared and must be regenerated with text-embedding-v3 @ 1024 dims")


def verify_migration():
    """验证迁移结果"""
    print("\n[Migration] Verifying migration...")
    
    with _engine.connect() as conn:
        # 检查long_term_memory_p2
        try:
            result = conn.execute(text("""
                SELECT atttypmod 
                FROM pg_attribute 
                WHERE attrelid = 'long_term_memory_p2'::regclass 
                AND attname = 'embedding'
            """))
            row = result.fetchone()
            if row:
                dim = row.atttypmod
                if dim == 1024:
                    print(f"  ✓ long_term_memory_p2.embedding: vector({dim})")
                else:
                    print(f"  ✗ long_term_memory_p2.embedding: wrong dimension {dim}")
                    return False
        except Exception as e:
            print(f"  - long_term_memory_p2 not found (may not exist yet)")
        
        # 检查manual_metadata
        try:
            result = conn.execute(text("""
                SELECT atttypmod 
                FROM pg_attribute 
                WHERE attrelid = 'manual_metadata'::regclass 
                AND attname = 'embedding'
            """))
            row = result.fetchone()
            if row:
                dim = row.atttypmod
                if dim == 1024:
                    print(f"  ✓ manual_metadata.embedding: vector({dim})")
                else:
                    print(f"  ✗ manual_metadata.embedding: wrong dimension {dim}")
                    return False
        except Exception as e:
            print(f"  - manual_metadata not found (may not exist yet)")
    
    return True


def main():
    """主入口"""
    # 初始化数据库连接
    init_db()
    
    # 执行迁移
    try:
        migrate_vector_dimensions()
        
        # 验证
        if verify_migration():
            print("\n✓ Migration successful!")
        else:
            print("\n✗ Migration verification failed!")
            sys.exit(1)
    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
