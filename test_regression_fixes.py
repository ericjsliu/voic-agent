#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试验证脚本

验证三个Docker回归失败项的修复：
1. 主动记忆HTTP 500 - ChitchatAction格式正确
2. 导航回家无法解析 - 家/公司允许latitude=0
3. 向量搜索返回0 - embedding维度验证正确
"""

import sys
import asyncio
from datetime import datetime
import uuid


def test_chitchat_action_schema():
    """测试1：ChitchatAction schema正确"""
    print("\n" + "="*60)
    print("测试1: ChitchatAction schema验证")
    print("="*60)
    
    from app.schemas.taskgraph import ChitchatAction, ActionLevel
    
    # 正确的格式：只有response字段
    try:
        action = ChitchatAction(response="好的，已记住这个地址", level=ActionLevel.L0)
        print(f"✓ ChitchatAction创建成功: {action}")
        print(f"  response: {action.response}")
        print(f"  level: {action.level}")
        return True
    except Exception as e:
        print(f"✗ ChitchatAction创建失败: {e}")
        return False


def test_chitchat_action_in_taskgraph():
    """测试1+: TaskGraph包含ChitchatAction"""
    print("\n" + "="*60)
    print("测试1+: TaskGraph with ChitchatAction")
    print("="*60)
    
    from app.schemas.taskgraph import TaskGraph, Task, Step, DomainType, ChitchatAction, ActionLevel
    
    try:
        taskgraph = TaskGraph(
            trace_id=str(uuid.uuid4()),
            session_id="test_session",
            timestamp=datetime.utcnow().isoformat() + "Z",
            tasks=[
                Task(
                    task_id="test_task",
                    steps=[
                        Step(
                            step_id="s_memory_confirm",
                            domain=DomainType.CHITCHAT,
                            action=ChitchatAction(response="好的，已记住这个地址", level=ActionLevel.L0),
                            depends_on=[]
                        )
                    ]
                )
            ]
        )
        print(f"✓ TaskGraph创建成功")
        print(f"  trace_id: {taskgraph.trace_id}")
        print(f"  session_id: {taskgraph.session_id}")
        print(f"  tasks[0].steps[0].action: {taskgraph.tasks[0].steps[0].action}")
        
        # 验证可以序列化
        json_data = taskgraph.model_dump_json()
        print(f"✓ TaskGraph可以序列化为JSON (长度: {len(json_data)})")
        return True
    except Exception as e:
        print(f"✗ TaskGraph创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_nav_validation_with_home_address():
    """测试2：家/公司导航验证允许latitude=0"""
    print("\n" + "="*60)
    print("测试2: 导航验证允许家/公司latitude=0")
    print("="*60)
    
    from app.schemas.taskgraph import (
        TaskGraph, Task, Step, DomainType, 
        NavigationAction, NavGoal, RoutePreferences, ActionLevel
    )
    from app.planner.planner import Planner
    
    # 创建导航到家的TaskGraph（latitude=0）
    try:
        taskgraph = TaskGraph(
            trace_id=str(uuid.uuid4()),
            session_id="test_session",
            timestamp=datetime.utcnow().isoformat() + "Z",
            tasks=[
                Task(
                    task_id="test_nav_task",
                    steps=[
                        Step(
                            step_id="s_nav_home",
                            domain=DomainType.NAVIGATION,
                            action=NavigationAction(
                                action="set_nav_goal",
                                goal=NavGoal(
                                    poi_name="家",
                                    latitude=0.0,  # 云端不返回坐标
                                    longitude=0.0,
                                    address="北京市朝阳区望京SOHO"  # 只发address_text
                                ),
                                route_prefs=RoutePreferences(),
                                level=ActionLevel.L0
                            ),
                            depends_on=[]
                        )
                    ]
                )
            ]
        )
        
        print(f"✓ 创建导航到家的TaskGraph成功")
        print(f"  poi_name: {taskgraph.tasks[0].steps[0].action.goal.poi_name}")
        print(f"  latitude: {taskgraph.tasks[0].steps[0].action.goal.latitude}")
        print(f"  address: {taskgraph.tasks[0].steps[0].action.goal.address}")
        
        # 测试_validate_safety不会抛出异常
        planner = Planner()
        try:
            planner._validate_safety(taskgraph)
            print(f"✓ 导航验证通过（家/公司允许latitude=0）")
            return True
        except ValueError as e:
            print(f"✗ 导航验证失败: {e}")
            return False
            
    except Exception as e:
        print(f"✗ 创建TaskGraph失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_embedding_validation():
    """测试3：Embedding维度验证"""
    print("\n" + "="*60)
    print("测试3: Embedding维度验证")
    print("="*60)
    
    import os
    
    # 检查环境变量
    embed_model = os.getenv('MEMORY_EMBED_MODEL', 'text-embedding-v3')
    embed_dim = int(os.getenv('EMBEDDING_DIMENSIONS', '1024'))
    
    print(f"  MEMORY_EMBED_MODEL: {embed_model}")
    print(f"  EMBEDDING_DIMENSIONS: {embed_dim}")
    
    # 测试QwenEmbedding的维度验证逻辑
    try:
        from app.memory.qwen_clients import QwenEmbedding
        
        # 创建embedding客户端（不实际调用API）
        client = QwenEmbedding(model=embed_model)
        print(f"✓ QwenEmbedding客户端初始化成功")
        print(f"  model: {client.model}")
        print(f"  dimension: {client.dimension}")
        
        # 验证dimension设置正确
        if client.dimension == 1024:
            print(f"✓ Embedding维度设置正确: {client.dimension}")
        else:
            print(f"✗ Embedding维度错误: expected 1024, got {client.dimension}")
            return False
        
        # 测试空文本检测
        result = client.embed("")
        if result is None:
            print(f"✓ 空文本检测正确: 返回None而非0维向量")
        else:
            print(f"✗ 空文本检测失败: 应返回None")
            return False
        
        # 测试P2MemoryService的embedding生成逻辑
        from app.memory.p2_memory_service import P2MemoryService
        
        # 创建服务（vector disabled，不需要实际API）
        service = P2MemoryService(enable_vector=False)
        print(f"✓ P2MemoryService初始化成功 (vector disabled)")
        
        # 测试_generate_embedding的空文本检测
        result = service._generate_embedding("")
        if result is None:
            print(f"✓ P2MemoryService空文本检测正确")
        else:
            print(f"✗ P2MemoryService空文本检测失败")
            return False
        
        return True
        
    except Exception as e:
        print(f"✗ Embedding测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_vector_column():
    """测试3+: 数据库vector列定义"""
    print("\n" + "="*60)
    print("测试3+: 数据库vector列验证")
    print("="*60)
    
    try:
        from app.storage.models import LongTermMemoryP2, ManualMetadata
        from sqlalchemy import inspect
        
        # 检查LongTermMemoryP2的embedding列
        print(f"✓ LongTermMemoryP2.embedding 列定义:")
        print(f"  类型: {LongTermMemoryP2.embedding.type}")
        
        # 检查ManualMetadata的embedding列
        print(f"✓ ManualMetadata.embedding 列定义:")
        print(f"  类型: {ManualMetadata.embedding.type}")
        
        return True
    except Exception as e:
        print(f"✗ 数据库模型检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试流程"""
    print("\n" + "="*80)
    print("Docker回归测试修复验证")
    print("="*80)
    
    results = {}
    
    # 测试1：ChitchatAction schema
    results['chitchat_schema'] = test_chitchat_action_schema()
    results['chitchat_taskgraph'] = test_chitchat_action_in_taskgraph()
    
    # 测试2：导航验证
    results['nav_validation'] = test_nav_validation_with_home_address()
    
    # 测试3：Embedding维度
    results['embedding_validation'] = test_embedding_validation()
    results['database_vector'] = test_database_vector_column()
    
    # 汇总结果
    print("\n" + "="*80)
    print("测试结果汇总")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n✓ 所有回归测试修复验证通过！")
        return 0
    else:
        print(f"\n✗ {total - passed} 个测试失败")
        return 1


if __name__ == '__main__':
    sys.exit(main())
