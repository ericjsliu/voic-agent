# 被动记忆Batch Job实现指南

## PRD v1.28 架构概述

### 流程分离

1. **对话回合（实时）**
   - 用户utterance → Planner → Orchestrator → Taskgraph执行
   - 入队hot candidate到Redis（可选，短TTL 30分钟）
   - **不触发Memory.put**

2. **Task终态（实时）**
   - 持久化到PG audit/task store（trace events, task results）
   - **不触发Memory.put**
   - 供batch job稍后扫描

3. **被动提取（离线Batch）**
   - Scheduled batch job（nightly或每N小时）
   - 扫描PG新task records
   - 可选参考Redis hot candidates
   - Score≥0.7 → 10-class → Memory.put

### 设计原则

- **实时路径轻量化**: 对话/task不阻塞在Memory.put上
- **可重放**: PG audit store保留完整trace → batch job可重跑
- **渐进式**: 可先用Redis队列，后续切换到纯PG扫描

---

## Batch Job实现方案

### 方案A: Redis队列为主（当前实现）

**适用场景**: 快速上线，Redis已就绪

```python
# batch_job.py (伪代码)
from app.memory.passive_queue import PassiveMemoryConsumer
from app.memory.p2_memory_service import P2MemoryService

def run_passive_extraction_batch():
    """被动记忆批量提取任务（cron/celery/APScheduler）"""
    
    # 1. 获取所有user_id（从session/user表）
    user_ids = get_active_user_ids()
    
    # 2. 逐user消费Redis队列
    p2_service = P2MemoryService(enable_vector=True)
    consumer = PassiveMemoryConsumer(queue, p2_service)
    
    for user_id in user_ids:
        result = consumer.consume_for_user(
            user_id=user_id,
            trigger_reason="scheduled_batch"
        )
        print(f"[Batch] User {user_id}: {result}")
    
    # 3. 清理过期Redis keys（TTL自动处理）
```

**优点**:
- 实现简单，当前代码已支持
- Redis自动TTL清理

**缺点**:
- Redis丢失后无法重放
- 依赖Redis可用性

---

### 方案B: PG audit store扫描（推荐生产）

**适用场景**: 生产环境，需要可重放

```python
# batch_job_pg.py (伪代码)
from app.audit import get_audit_logger
from app.memory.p2_memory_service import P2MemoryService

def run_passive_extraction_from_pg():
    """从PG audit store扫描task records提取记忆"""
    
    # 1. 扫描最近N小时的trace events（按trace_id分组）
    traces = scan_recent_traces(hours=24)
    
    # 2. 对每个trace重构utterance + response
    p2_service = P2MemoryService(enable_vector=True)
    
    for trace in traces:
        # 检查是否已处理过（memory_extraction_done标记）
        if is_trace_processed(trace.trace_id):
            continue
        
        # 提取utterance + assistant response
        utterance = extract_utterance_from_trace(trace)
        response = extract_response_from_trace(trace)
        user_id = trace.metadata.get('user_id')
        
        # 调用passive extraction
        p2_service.handle_passive_extraction(
            user_id=user_id,
            utterance=utterance,
            assistant_response=response,
            context={},
            trace_id=trace.trace_id
        )
        
        # 标记已处理
        mark_trace_processed(trace.trace_id)
    
    print(f"[Batch] Processed {len(traces)} traces")
```

**优点**:
- 可重放（PG永久存储）
- 不依赖Redis
- 可追溯审计

**缺点**:
- 需要设计trace扫描索引
- 需要utterance/response重构逻辑

---

## 调度方式

### 方案1: Cron（简单）

```bash
# /etc/cron.d/passive-memory-batch
0 2 * * * cd /app && python -m scripts.batch_job_passive_memory
```

### 方案2: Celery Beat（推荐）

```python
# celeryconfig.py
from celery.schedules import crontab

beat_schedule = {
    'passive-memory-extraction': {
        'task': 'tasks.passive_memory_batch',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点
    },
}
```

### 方案3: APScheduler（内嵌）

```python
# app/main.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(
    run_passive_extraction_batch,
    'cron',
    hour=2,
    minute=0,
    id='passive_memory_batch'
)
scheduler.start()
```

---

## 监控与告警

### 关键指标

1. **Batch job执行时长**
   - 目标: <10分钟（每天）
   - 告警: >30分钟

2. **处理的trace/candidate数量**
   - 记录每次batch处理的records数
   - 异常检测: 突然下降或激增

3. **Memory.put成功率**
   - 统计score≥0.7的比例
   - 统计安全门闩BLOCK率

4. **队列堆积**
   - Redis队列长度（如使用方案A）
   - 预警: >10000条/user

### 日志示例

```
[Batch 2026-09-17T02:00:00Z] Start passive memory extraction
[Batch] Scanned 1250 traces from PG audit store
[Batch] User account_default:driver_001: 12 candidates → 5 extracted → 3 put
[Batch] User account_default:driver_002: 8 candidates → 2 extracted → 1 put
[Batch] Total: 1250 traces → 780 candidates → 320 extracted → 150 put
[Batch] Duration: 6m32s
[Batch] End
```

---

## 迁移路径

### Phase 1: Redis队列（当前PR）
- ✅ 对话回合入队到Redis
- ✅ 主动记忆sync put（不变）
- ✅ Consumer接口就绪
- ⏳ Batch job脚本（待实现）

### Phase 2: 添加batch调度
- 实现`scripts/batch_job_passive_memory.py`
- 配置cron/celery调度
- 监控指标接入

### Phase 3: PG扫描增强
- 设计trace扫描索引（`trace_id`, `created_at`, `processed`）
- 实现utterance/response重构
- 逐步迁移到纯PG扫描，弱化Redis依赖

---

## 测试验证

### 单元测试（已实现）
- ✅ Redis队列入队/获取/清空
- ✅ Consumer逻辑
- ✅ 验证per-turn不put
- ✅ 验证主动记忆仍put

### 集成测试（待补充）
```python
def test_batch_job_end_to_end():
    """端到端测试batch job"""
    # 1. 模拟对话，入队3条candidates
    # 2. 运行batch job
    # 3. 验证Memory.put被调用3次
    # 4. 验证队列已清空
```

### 线上验证
1. 观察Redis队列长度曲线（应在batch后归零）
2. 观察PG long_term_memory_p2表新增记录（batch后增加）
3. 对比batch前后的memory召回效果

---

## FAQ

**Q: 为什么不在Task-end立即put？**
A: PRD v1.28设计原则：实时路径轻量化。Task-end只持久化audit/task store（快速），Memory.put由batch离线处理（score+extract+10-class较重）。

**Q: Redis队列丢失怎么办？**
A: 短期可接受（30分钟TTL）；长期迁移到PG扫描（方案B）实现可重放。

**Q: Batch频率如何选择？**
A: 建议nightly（凌晨2-4点低峰）。如需更及时，可每6小时一次，但注意避开高峰时段。

**Q: 主动记忆为什么不走batch？**
A: 用户显式「记住」需要立即反馈确认，必须同步put。被动提取无显式预期，可延迟。
