# Upgrade v1.37 - Locked Feature Package

**PRD Version**: v1.37 / COMPLETE  
**Date**: 2024-12-19  
**Status**: ✅ Implemented

---

## 概览

本次升级实现了三个关键功能，提升系统的可靠性、可恢复性和智能化水平：

1. **tool_use_id 幂等性** - 防止重复执行副作用
2. **检查点/恢复** - 支持中断后继续执行
3. **ACE 离线 Playbook** - 在线只读规则库，离线生成管道

---

## Feature 1: tool_use_id 幂等性

### 功能描述

每个 MQTT 下行/适配器执行步骤获得稳定的 `tool_use_id`（SHA256 哈希 of `trace_id:step_id`）。Gateway/Orchestrator 记录 `tool_use_id → result` 映射；重复下行/重试返回缓存的 ACK，**不重新执行车辆/媒体/导航副作用**。

### 架构变更

```
┌─────────────────────────────────────────────────────────┐
│ Orchestrator                                            │
│                                                         │
│  1. 生成 tool_use_id = SHA256(trace_id + step_id)     │
│  2. 检查 Redis: idempotency:{tool_use_id}             │
│     └─ 命中 → 返回缓存结果，跳过适配器执行            │
│     └─ 未命中 → 执行适配器 → 缓存结果 (TTL 24h)      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 核心模块

- **`app/orchestrator/idempotency.py`**: 幂等性管理器
  - `generate_tool_use_id(trace_id, step_id) -> str`
  - `get_cached_result(tool_use_id) -> Optional[Dict]`
  - `cache_result(tool_use_id, result) -> bool`

### 集成点

- **Orchestrator**: `_execute_step()` 方法
  1. 生成 `tool_use_id`
  2. 检查缓存 → 命中则返回，未命中则执行
  3. 执行后缓存结果

### Redis 键设计

```
idempotency:{tool_use_id}
  ├─ Value: JSON {"status": "completed", "result": {...}}
  └─ TTL: 86400s (24小时)
```

### 示例：防止重复执行

```python
# 场景：网络超时重试
# trace_id = "abc123", step_id = "step_1"

# 第一次执行
tool_use_id = SHA256("abc123:step_1")  # → "7f3a..."
result = await adapter.execute(step)  # 实际执行，打开车窗
redis.setex(f"idempotency:{tool_use_id}", 86400, json.dumps(result))

# 网络超时，客户端重试（相同 trace_id + step_id）
tool_use_id = SHA256("abc123:step_1")  # → "7f3a..." (相同)
cached = redis.get(f"idempotency:{tool_use_id}")  # 命中
# → 返回缓存结果，**不再调用 adapter.execute**，车窗不会重复打开
```

### 测试

```bash
pytest tests/test_v137_idempotency.py -v
```

**测试覆盖**：
- ✅ tool_use_id 生成稳定性（相同输入 → 相同 ID）
- ✅ SHA256 哈希正确性
- ✅ 缓存命中/未命中逻辑
- ✅ 重复步骤返回缓存，不重新执行

---

## Feature 2: 检查点/恢复

### 功能描述

持久化 TaskGraph + Orchestrator 节点进度到 Redis（和可选的 PostgreSQL）。在重连/恢复会话时，从最后检查点继续执行，而非从头重新规划（除非用户取消）。

### 架构变更

```
┌─────────────────────────────────────────────────────────┐
│ 对话回合                                                │
│  1. Planner 生成 TaskGraph                             │
│  2. Orchestrator 执行步骤                              │
│     └─ 每步完成后 → 保存检查点到 Redis                │
│  3. 网络中断 / 客户端断线                              │
│                                                         │
│ 恢复会话                                                │
│  1. Client: POST /session/{id}/resume                  │
│  2. Orchestrator 加载检查点                            │
│  3. 恢复 TaskGraph + StepStates                        │
│  4. 跳过已完成步骤，继续执行未完成步骤                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 核心模块

- **`app/orchestrator/checkpoint.py`**: 检查点管理器
  - `save_checkpoint(session_id, trace_id, ...) -> bool`
  - `load_checkpoint(session_id, trace_id) -> Optional[CheckpointData]`
  - `resume_from_checkpoint(session_id, trace_id) -> Optional[Dict]`

### 检查点数据结构

```python
CheckpointData:
  - session_id: str
  - trace_id: str
  - taskgraph: Dict  # TaskGraph 序列化
  - step_states: Dict[str, StepState]  # step_id → 状态
  - completed_steps: List[str]
  - failed_steps: List[str]
  - shadow_state: Dict
  - timestamp: str
```

### Redis 键设计

```
checkpoint:{session_id}:{trace_id}
  ├─ Value: JSON CheckpointData
  └─ TTL: 3600s (1小时，足够处理临时断线)
```

### API 端点

#### 1. 恢复会话

```http
POST /session/{session_id}/resume?trace_id={trace_id}

# Response:
{
  "success": true,
  "session_id": "session_123",
  "result": {
    "resumed_from_checkpoint": true,
    "trace_id": "trace_456",
    "task_results": {...}
  }
}
```

#### 2. 列出检查点

```http
GET /session/{session_id}/checkpoints

# Response:
{
  "session_id": "session_123",
  "count": 2,
  "checkpoints": [
    {
      "trace_id": "trace_456",
      "timestamp": "2024-01-01T12:00:00Z",
      "completed_steps": 2,
      "failed_steps": 0
    }
  ]
}
```

### 恢复逻辑

1. **加载检查点**: 从 Redis 读取 `checkpoint:{session_id}:{trace_id}`
2. **恢复状态**: 
   - TaskGraph → `orchestrator.pending_downlink`
   - StepStates → `orchestrator.task_states`
   - 已完成步骤 → 标记为 `StepStatus.COMPLETED`
3. **继续执行**: 
   - `_get_ready_steps()` 自动跳过已完成步骤
   - 只执行 `pending` 或 `ready` 状态的步骤
4. **清理**: 任务完成后删除检查点

### 示例：中断恢复

```python
# 场景：多步骤任务执行中断

# 初始执行
taskgraph = TaskGraph(
    trace_id="trace_123",
    tasks=[
        Task(steps=[
            Step(id="step_1"),  # 打开车窗
            Step(id="step_2", depends_on=["step_1"]),  # 播放音乐
            Step(id="step_3", depends_on=["step_2"])   # 导航到机场
        ])
    ]
)

# step_1 完成后保存检查点
# → Redis: checkpoint:session_123:trace_123 = {completed_steps: ["step_1"]}

# 网络中断，客户端断线

# --- 用户重连 ---

# 恢复会话
POST /session/session_123/resume

# 系统行为：
# 1. 加载检查点 → step_1 已完成
# 2. 跳过 step_1，直接执行 step_2 (依赖已满足)
# 3. 继续执行 step_3
# 4. 完成后清除检查点
```

### 测试

```bash
pytest tests/test_v137_checkpoint.py -v
```

**测试覆盖**：
- ✅ 检查点保存/加载
- ✅ 多步骤中断恢复
- ✅ 已完成步骤跳过
- ✅ 检查点清理

---

## Feature 3: ACE 离线 Playbook

### 功能描述

最小化 Playbook 存储（文件或 PostgreSQL）：
- **在线**：Assemble/Planner 可读取静态快照（read-only）
- **离线**：Generator/Reflector/Curator 接口 + 脚本/作业追加 Delta 条目
- **合并**：仅通过显式提升到快照（即使提升目前是 CLI）
- **不混淆**：与每轮长期记忆写入分离

### 架构变更

```
┌──────────────────────────────────────────────────────────┐
│ 在线路径 (Planner)                                       │
│  1. Planner 读取 Playbook 快照 (read-only)              │
│  2. 搜索相关规则 → 注入到 LLM 上下文                     │
│  3. LLM 生成 TaskGraph 时参考规则                       │
│                                                          │
│ 离线管道 (Batch Job / CLI)                              │
│  1. Generator: 从审计事件中生成 Delta                   │
│  2. Reflector: 评估现有条目有效性                        │
│  3. Curator: 人工审核 Delta                              │
│  4. Promote: 批准的 Delta 提升到快照                     │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 核心模块

#### 1. Playbook 存储

**`app/playbook/store.py`**:
- `PlaybookStore`: 支持文件/PostgreSQL 后端
- `PlaybookEntry`: 规则条目（ID、章节、内容、置信度）
- `PlaybookSnapshot`: 快照版本

#### 2. 离线管道

**`app/playbook/offline_pipeline.py`**:
- `PlaybookGenerator`: 从审计事件/对话生成 Delta
- `PlaybookReflector`: 评估条目有效性
- `PlaybookCurator`: 审核和批准 Delta
- `PlaybookDelta`: Delta 条目（候选规则）

### Playbook 数据结构

#### PlaybookEntry (快照中的条目)

```python
{
  "id": "entry_1",
  "section": "vehicle_control",  # 章节
  "content": "车窗操作需要车辆静止",
  "helpful_count": 10,
  "harmful_count": 1,
  "confidence_score": 0.9,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z",
  "metadata": {}
}
```

#### PlaybookDelta (待审核条目)

```python
{
  "id": "delta_1",
  "section": "safety",
  "content": "学校区域禁止车辆控制",
  "confidence_score": 0.85,
  "created_at": "2024-01-01T00:00:00Z",
  "metadata": {},
  "reviewed": false,
  "approved": false
}
```

### 章节分类

```python
class PlaybookSection(Enum):
    VEHICLE_CONTROL = "vehicle_control"
    NAVIGATION = "navigation"
    MEDIA = "media"
    SAFETY = "safety"
    USER_PREFERENCES = "user_preferences"
    TROUBLESHOOTING = "troubleshooting"
```

### 文件存储结构

```
/tmp/playbook_snapshot.json  # 生产快照 (在线只读)
/tmp/playbook_deltas.json    # 待审核 Delta (离线写入)
```

**环境变量**:
```bash
PLAYBOOK_BACKEND=file  # 或 postgres
PLAYBOOK_SNAPSHOT_PATH=/tmp/playbook_snapshot.json
PLAYBOOK_DELTA_PATH=/tmp/playbook_deltas.json
```

### API 端点

#### 1. 获取快照

```http
GET /playbook/snapshot

# Response:
{
  "version": "v1.0",
  "snapshot_time": "2024-01-01T00:00:00Z",
  "total_entries": 5,
  "entries": [...]
}
```

#### 2. 按章节查询

```http
GET /playbook/section/vehicle_control?min_confidence=0.8

# Response:
{
  "section": "vehicle_control",
  "min_confidence": 0.8,
  "count": 3,
  "entries": [...]
}
```

#### 3. 搜索规则

```http
GET /playbook/search?q=车窗&section=vehicle_control&limit=5

# Response:
{
  "query": "车窗",
  "section": "vehicle_control",
  "count": 2,
  "entries": [...]
}
```

### Planner 集成

Planner 自动注入 Playbook 上下文到 LLM：

```python
# app/planner/planner.py

async def plan(user_utterance, context, playbook_store=None):
    # 搜索相关 Playbook 条目
    entries = playbook_store.search_entries(user_utterance, limit=3)
    
    # 注入到 LLM 上下文
    context_str += "\n- Playbook参考：\n"
    for entry in entries:
        context_str += f"  • [{entry.section}] {entry.content}\n"
    
    # LLM 生成 TaskGraph 时参考规则
    ...
```

### 离线管道使用

#### 1. 生成 Delta

```python
from app.playbook.offline_pipeline import FileBasedGenerator

generator = FileBasedGenerator("/tmp/playbook_deltas.json")

# 从审计事件生成
audit_events = [...]  # 查询 PG audit_events 表
deltas = generator.generate_from_audit_events(audit_events, min_confidence=0.7)

# 保存 Delta
generator.save_deltas(deltas)
```

#### 2. 审核 Delta

```python
from app.playbook.offline_pipeline import FileBasedCurator

curator = FileBasedCurator("/tmp/playbook_deltas.json")

# 列出待审核
pending = curator.list_pending_deltas(min_confidence=0.7)

# 审核单个 Delta
curator.review_delta(
    delta_id="delta_1",
    reviewer="admin",
    approved=True,
    notes="规则合理"
)
```

#### 3. 提升到快照

```python
from app.playbook.store import PlaybookStore

store = PlaybookStore(backend="file")

# 提升批准的 Delta
approved_ids = ["delta_1", "delta_3", "delta_5"]
store.promote_deltas_to_snapshot(approved_ids, new_version="v1.1")

# 快照更新后，在线 Planner 自动使用新版本
```

### CLI 脚本示例

创建 `scripts/playbook_promote.py`:

```python
#!/usr/bin/env python3
"""Playbook Delta 提升脚本"""

import argparse
from app.playbook.store import PlaybookStore
from app.playbook.offline_pipeline import FileBasedCurator

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", action="store_true", help="审核 Delta")
    parser.add_argument("--promote", nargs="+", help="提升 Delta ID")
    parser.add_argument("--version", required=True, help="新版本号")
    args = parser.parse_args()
    
    curator = FileBasedCurator()
    store = PlaybookStore(backend="file")
    
    if args.review:
        pending = curator.list_pending_deltas()
        print(f"待审核 Delta: {len(pending)}")
        for delta in pending:
            print(f"  {delta.id}: {delta.content[:50]}...")
    
    if args.promote:
        store.promote_deltas_to_snapshot(args.promote, args.version)
        print(f"已提升 {len(args.promote)} 个 Delta 到版本 {args.version}")

if __name__ == "__main__":
    main()
```

使用：

```bash
# 审核待处理 Delta
python scripts/playbook_promote.py --review

# 提升批准的 Delta
python scripts/playbook_promote.py --promote delta_1 delta_2 --version v1.1
```

### 测试

```bash
pytest tests/test_v137_playbook.py -v
```

**测试覆盖**：
- ✅ 快照读取（文件后端）
- ✅ 按章节/搜索查询
- ✅ 离线 Generator/Reflector/Curator
- ✅ Delta 提升到快照
- ✅ Planner 集成

---

## 运行测试

### 所有 v1.37 测试

```bash
pytest tests/test_v137_*.py -v
```

### 按功能测试

```bash
# Feature 1: 幂等性
pytest tests/test_v137_idempotency.py -v

# Feature 2: 检查点
pytest tests/test_v137_checkpoint.py -v

# Feature 3: Playbook
pytest tests/test_v137_playbook.py -v
```

### 集成测试

```bash
# 完整回归测试（确保不破坏现有功能）
pytest tests/ -v -m "not slow"
```

---

## 配置要求

### 环境变量

```bash
# Redis (必需 - 用于幂等性和检查点)
REDIS_URL=redis://localhost:6379/0

# PostgreSQL (可选 - 用于检查点持久化)
DATABASE_URL=postgresql://cockpit:cockpit@localhost:5432/cockpit_agent

# Playbook (可选)
PLAYBOOK_BACKEND=file  # 或 postgres
PLAYBOOK_SNAPSHOT_PATH=/tmp/playbook_snapshot.json
PLAYBOOK_DELTA_PATH=/tmp/playbook_deltas.json
```

### Redis 版本

- 最低版本: **Redis 5.0+**
- 推荐版本: **Redis 7.0+**

### 内存需求

- **幂等性缓存**: ~10KB per tool_use_id × 预期并发数
- **检查点**: ~50KB per active session × 预期会话数
- **Playbook**: 快照加载到内存，预计 < 1MB

---

## 迁移指南

### 从 v1.36 升级到 v1.37

1. **更新依赖** (无新增依赖)

```bash
# 现有依赖足够，无需额外安装
```

2. **启动 Redis**

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

3. **（可选）初始化 Playbook**

创建初始快照：

```bash
cat > /tmp/playbook_snapshot.json << 'EOF'
{
  "version": "v1.0",
  "snapshot_time": "2024-01-01T00:00:00Z",
  "total_entries": 0,
  "entries": []
}
EOF
```

4. **重启服务**

```bash
# Docker Compose
docker-compose restart agent

# 本地开发
python -m app.main
```

5. **验证功能**

```bash
# 检查健康状态
curl http://localhost:8000/health

# 测试 Playbook API
curl http://localhost:8000/playbook/snapshot

# 测试检查点 API
curl http://localhost:8000/session/{session_id}/checkpoints
```

---

## 回滚指南

如果升级后遇到问题，可安全回滚：

1. **代码回滚**

```bash
git checkout v1.36
docker-compose build
docker-compose up -d
```

2. **数据清理** (可选)

```bash
# 清理 Redis 检查点和幂等性缓存
redis-cli
> DEL checkpoint:*
> DEL idempotency:*
```

**注意**: 
- 幂等性和检查点数据是临时的（TTL 1-24小时），回滚不影响长期数据
- Playbook 快照是只读的，回滚不影响现有快照

---

## 性能影响

### 幂等性

- **Redis 开销**: 每步骤 2 次 Redis 调用（GET + SETEX）
- **延迟增加**: < 1ms (本地 Redis)
- **内存占用**: ~10KB per tool_use_id

### 检查点

- **保存频率**: 每步骤完成后
- **Redis 开销**: 每步骤 1 次 SETEX
- **延迟增加**: < 2ms (本地 Redis)
- **内存占用**: ~50KB per checkpoint

### Playbook

- **加载开销**: 启动时一次性加载快照到内存
- **查询延迟**: < 1ms (内存搜索)
- **内存占用**: < 1MB (预计 100-500 条规则)

### 总体影响

- **吞吐量**: 无显著影响（< 5% 下降）
- **P99 延迟**: +2-3ms
- **内存**: +10-20MB per 100 concurrent sessions

---

## 故障排查

### Feature 1: 幂等性

**问题**: 步骤重复执行  
**排查**:
```bash
# 检查 Redis 连接
redis-cli PING

# 检查 idempotency 键
redis-cli KEYS "idempotency:*"

# 查看缓存内容
redis-cli GET "idempotency:{tool_use_id}"
```

### Feature 2: 检查点

**问题**: 恢复失败  
**排查**:
```bash
# 检查检查点是否存在
redis-cli KEYS "checkpoint:{session_id}:*"

# 查看检查点内容
redis-cli GET "checkpoint:{session_id}:{trace_id}"

# API 查询
curl http://localhost:8000/session/{session_id}/checkpoints
```

### Feature 3: Playbook

**问题**: 快照加载失败  
**排查**:
```bash
# 检查文件存在
ls -lh /tmp/playbook_snapshot.json

# 验证 JSON 格式
jq . /tmp/playbook_snapshot.json

# API 查询
curl http://localhost:8000/playbook/snapshot
```

---

## 已知限制

### Feature 1: 幂等性

- **TTL**: 缓存 24 小时后过期，超长时间重试可能失效
- **Redis 故障**: Redis 不可用时幂等性失效，但不影响功能（降级为无幂等性）

### Feature 2: 检查点

- **TTL**: 检查点 1 小时后过期，超时无法恢复
- **并发**: 同一 session_id 并发执行可能覆盖检查点

### Feature 3: Playbook

- **在线只读**: 在线路径不能写入 Playbook，必须通过离线管道
- **搜索精度**: 当前使用简单关键词匹配，未来可升级为向量搜索

---

## 未来增强

### 短期 (v1.38)

- [ ] PostgreSQL 检查点持久化（当前仅 Redis）
- [ ] Playbook 向量搜索（提升相关性）
- [ ] 检查点压缩（减少内存占用）

### 中期 (v1.39-1.40)

- [ ] 分布式幂等性（多实例部署）
- [ ] Playbook A/B 测试（多版本快照）
- [ ] 自动 Playbook 生成（定期批处理）

### 长期 (v2.0+)

- [ ] Playbook 协同编辑（Web UI）
- [ ] 强化学习优化规则
- [ ] 联邦学习跨车型共享

---

## 总结

v1.37 升级引入了三个核心功能，显著提升系统的**可靠性**（幂等性）、**可恢复性**（检查点）和**智能化**（Playbook）。所有功能设计为**向后兼容**，可选启用，不破坏现有流程。

**关键收益**:
- ✅ 防止网络重试导致的重复副作用
- ✅ 支持中断后无缝恢复执行
- ✅ 规划器可参考离线积累的最佳实践

**生产就绪度**: ⭐⭐⭐⭐ (4/5)
- 需要 Redis 稳定运行
- 建议生产环境启用 Redis 持久化（AOF 或 RDB）
- Playbook 功能可选，不影响核心流程

---

**Questions?** 参考 `tests/test_v137_*.py` 中的测试用例。
