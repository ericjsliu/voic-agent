# 一致性审计报告 - COMPLETE-v1.0 vs 当前实现

**审计日期**: 2026-09-17  
**设计基线**: cockpit-voice-agent-tech-design-COMPLETE-v1.0  
**代码分支**: main  
**审计范围**: 7个硬约束 + 其他重大不匹配

---

## 执行摘要

| 约束项 | 状态 | 优先级 |
|--------|------|--------|
| 1. NavGoal address_text 下发 | **PARTIAL** | P1 |
| 2. P2 Memory 批抽/dedupe/季节 | **GAP** | P0 |
| 3. profile_ready 门控 | **OK** | - |
| 4. L2 确认门闩 | **OK** | - |
| 5. WebSocket `/ws/{session_id}` | **OK** | - |
| 6. Vector CAST + dimensions | **OK** | - |
| 7. Recall 注入 Planner | **GAP** | P0 |

**需立即修复**: 2 个 GAP（优先级 P0）  
**需优化改进**: 1 个 PARTIAL（优先级 P1）  
**已正确实现**: 4 个 OK

---

## 1. NavGoal / 家导航：仅下发 address_text

### 设计意图（§9.1）
> 1. 云端**只下发** NavGoal（目的地意图 + `route_prefs` 地图 SDK 枚举）。
> 2. 「回家/去公司」：从长期记忆读 **地址文本** → 写入 `destination.address_text` → 下发端侧。
> 3. 云端**禁止** geocode；**禁止**写死假家/假 POI/假坐标兜底。
> 4. **端侧地图 SDK** 负责解析地址与算路。
> 5. 无记忆 → **追问**用户；不得静默落到 mock 地址。
> 6. 坐标**不回写**记忆。

### 实现证据

**✓ 正确部分**:
- `app/adapters/navigation.py:106-140` - 从 P2 memory 读取家/公司地址
- `app/adapters/navigation.py:116-127` - 返回 address text，不调用 geocode API
- `app/planner/planner.py:230-244` - 无记忆时正确追问用户而非 mock

**⚠️ 部分问题**:
```python
# app/adapters/navigation.py:119-123
return {
    "poi_name": "家",
    "address": home_addr,
    "latitude": 0.0,  # ⚠️ 不应在响应中出现坐标字段
    "longitude": 0.0,
}
```

**设计期望**:
```python
# 应该只返回 address_text，不包含 lat/lng 字段
return {
    "poi_name": "家",
    "address": home_addr,
    # 不应有 latitude/longitude 字段
}
```

**❌ 遗留问题**:
- `app/adapters/navigation.py:12-16` - 仍保留 MOCK_POI_DATABASE（非家/公司）
  - **注**: 设计允许测试 POI mock，但生产应标记或移除

### 判定: **PARTIAL**

**影响**: 低风险 - latitude/longitude 为 0.0 不会误导车端，但与设计契约不完全一致

**修复优先级**: P1（非阻塞但应优化）

**修复建议**:
1. 家/公司响应中移除 `latitude`/`longitude` 字段
2. 为 MOCK_POI_DATABASE 添加 `# TODO: 生产环境移除` 注释
3. 更新 NavGoal schema 使 lat/lng 对家/公司可选

---

## 2. P2 Memory: 主动直写 + 批抽画像

### 设计意图（§10.1, §10.5）
> **主动「帮我记住」**: **同步直写**；过黑名单即可；**不弹确认**  
> **被动画像**: Task 终态写**流水** → **定时批抽**入长期库  
> **dedupe_key**: 从 action/params **运行时生成**；禁止维护「全世界实体白名单」  
> **情境化偏好**: content 带条件（如「夏天空调偏好：26℃」）；dedupe_key 带季节/时段维度

### 实现证据

**✓ 正确部分**:
- `app/main.py:426-472` - 主动记忆同步直写，无确认 ✓
- `app/memory/p2_memory_service.py:376-450` - `put_memory` 实现正确 ✓
- `app/memory/p2_memory_service.py:64-90` - SafetyGate 硬黑名单过滤 ✓

**❌ GAP: 被动提取不是批抽**:
```python
# app/main.py:625-649 - 错误：每回合触发被动提取
async def passive_extraction():
    """被动记忆提取"""
    try:
        # ... 每次对话都调用 handle_passive_extraction
        app_state.p2_memory_service.handle_passive_extraction(...)
```

**设计期望**:
```python
# 应该：Task 终态写流水表 → 定时批作业扫描 → 7天≥2次才写长期记忆
# 1. Orchestrator 写任务流水（非长期记忆）
# 2. 独立批作业（cron）扫描流水 → 评分 → 聚合 → 写入
```

**❌ GAP: 无 dedupe_key 实现**:
- `app/memory/p2_memory_service.py` 全文搜索无 `dedupe_key` 相关代码
- 设计要求运行时生成（如 `pref:ac_temp:summer`），当前实现缺失

**❌ GAP: 无季节化偏好**:
- `app/memory/p2_memory_service.py` 无季节/时段判断逻辑
- content 格式化未包含季节条件（如「夏天空调偏好：26℃」）

**⚠️ 逻辑问题**:
```python
# app/memory/p2_memory_service.py:411-426 - 去重逻辑过于简单
for mem in existing:
    if self._similarity(mem.content, cleaned) > 0.95:
        # 仅靠字符串相似度，无法处理季节化偏好的冲突/共存
```

### 判定: **GAP**

**影响**: 高风险 - 违反架构约束
- 每回合写长期库会污染数据（临时/低价值信息）
- 无 dedupe_key 导致重复记忆堆积
- 无季节化导致冬夏偏好互相覆盖

**修复优先级**: P0（阻塞 P2 里程碑）

**修复建议**:
1. **立即**: 移除 `main.py:625-649` 的每回合被动提取
2. **新增**: 任务流水表（`task_ledger`）+ Task 终态写入
3. **新增**: 批作业 cron（默认每日）扫描流水 → 聚合 → 写入
4. **新增**: `dedupe_key` 生成逻辑（domain/action/params → key）
5. **新增**: 季节判断（用户时区 → 当前季节 → content/dedupe_key 带季节）

---

## 3. profile_ready 门控: 零 MQTT 下发

### 设计意图（§5.3, §7.2）
> `profile_state != ready` → **零下发**  
> 换车型/OTA：`profile_switching` → **暂停全部下行执行边** → 绑定新 Profile → `profile_ready`

### 实现证据

**✓ 完全正确**:
```python
# app/main.py:557-600
if not app_state.session_manager.is_profile_ready(session_info.session_id):
    profile_state = app_state.session_manager.get_profile_state(session_id)
    print(f"[Agent] MQTT downlink BLOCKED: profile state = {profile_state}")
    # ... audit event dispatch_blocked
    return  # ✓ 零执行边

# 再次检查（防止执行期间切换）
if not app_state.session_manager.is_profile_ready(session_info.session_id):
    # ... audit event dispatch_blocked
    return  # ✓ 二次门控
```

**架构符合**:
- 双重检查（执行前+执行后）
- profile_switching 期间完全阻断
- audit trail 记录所有 blocked 事件

### 判定: **OK**

---

## 4. L2: 确认前零执行，超时取消不重规划

### 设计意图（§6.2, Fig-S2）
> - 话术：短复述 + 明确是/否（例：「确认要锁车吗？」）
> - 超时：**15 秒**无明确接受 → 取消该 step，`reason=confirm_timeout`，轻提示「已取消」
> - 超时/取消后**不**自动重试、**不**回模型再问一轮

### 实现证据

**✓ 完全正确**:
```python
# app/orchestrator/orchestrator.py:293-330
if action_level == ActionLevel.L2:
    step_state.status = StepStatus.WAITING_CONFIRM  # ✓ 不执行
    step_state.confirm_deadline = datetime.utcnow() + timedelta(
        seconds=self.L2_CONFIRM_TIMEOUT  # 15秒
    )
    # ... 发送确认请求 writeback
    print(f"L2 action {step.step_id} waiting for confirmation (NOT executing)")
    return  # ✓ 零执行边

# 超时处理: lines 205-227
if state.confirm_deadline and now > state.confirm_deadline:
    state.status = StepStatus.CONFIRM_TIMEOUT
    state.error = "L2 confirmation timeout - cancelled"
    # ... 发送 confirm_timeout writeback
    print(f"L2 timeout {step_id} - CANCELLED, no re-ask")  # ✓ 不回模型
```

**架构符合**:
- L2 确认前 MQTT 零帧（orchestrator 不调用 execute）
- 超时 15 秒
- 取消后不回 Planner 重新规划
- 同图其他 L0/L1 step 不被阻塞（并行执行）

### 判定: **OK**

---

## 5. WebSocket `/ws/{session_id}` 注册

### 设计意图（§7.3, §13）
> 测试台 WebSocket **仅镜像**同一 writeback，不是车机主通道

### 实现证据

**✓ 完全正确**:
```python
# app/main.py:900-942
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await ws_manager.connect(websocket, session_id)
    # ... 接收 L2 确认
    # ... 广播 taskgraph 和 writeback
```

### 判定: **OK**

---

## 6. Vector Search: CAST AS vector + OpenAI v1 dimensions

### 设计意图（§10.7, §10.8）
> - 模型：`text-embedding-v3`
> - 维度：**1024**
> - 失败：search 空结果 + 日志，**不挡**主对话

### 实现证据

**✓ 完全正确**:
```python
# app/memory/p2_memory_service.py:496-509
emb_literal = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
sql = text("""
    SELECT memory_id, user_id, content, category, weight,
           embedding <=> CAST(:query_emb AS vector) AS distance  # ✓ CAST AS vector
    FROM long_term_memory_p2
    WHERE user_id = :user_id AND embedding IS NOT NULL
    ORDER BY distance
    LIMIT :top_k
""")

# app/memory/qwen_clients.py:250-273
response = self.client.embeddings.create(
    model=self.model,
    input=text,
    timeout=timeout,
    extra_body={"dimensions": self.dimension},  # ✓ dimensions via extra_body
)

# app/memory/qwen_clients.py:710-718
expected_dim = int(os.getenv('EMBEDDING_DIMENSIONS', '1024'))  # ✓ 1024维
if len(embedding) != expected_dim:
    print(f"ERROR: Invalid embedding dimension: {len(embedding)}, expected {expected_dim}")
    return None  # ✓ 绝不写入错误维度
```

**架构符合**:
- pgvector CAST AS vector 正确
- OpenAI v1 API 使用 extra_body 传递 dimensions
- 1024维强校验
- 失败降级不阻塞主对话

### 判定: **OK**

---

## 7. Recall 注入 Planner 提示词

### 设计意图（§8.1）
> Planner 输入五件套：
> 1. **系统提示**
> 2. **Capability Profile**
> 3. **运行时快照摘要**
> 4. **记忆切片**（KV 热点 + 向量 TopN≤5 / ≤300 token）
> 5. **用户话 + Session 热数据**

### 实现证据

**✓ 记忆已召回**:
```python
# app/session/context_assembler.py:140-169
memories = self.p2_memory_service.search_memories(
    user_id=user_id,
    query=query,
    top_k=5,
    token_budget=300
)
if memories:
    memory_slice["relevant_memories"] = [...]  # ✓ 召回成功
```

**❌ GAP: Planner 未使用 memory_slice**:
```python
# app/planner/planner.py:110-125 - user_message 构建
user_message = f"""用户输入：{user_utterance}

当前上下文：
- 位置：{context.current_location}
- 最近对话：{context.recent_utterances[-3:]}
- 车辆状态：{context.shadow_state}
# ❌ 缺失：context.memory_slice 未注入提示词

请分析用户意图，生成TaskGraph JSON。
```

**设计期望**:
```python
user_message = f"""用户输入：{user_utterance}

当前上下文：
- 位置：{context.current_location}
- 最近对话：{context.recent_utterances[-3:]}
- 车辆状态：{context.shadow_state}
- 用户偏好记忆：{context.memory_slice.get('relevant_memories', [])}  # ✓ 应注入

请结合用户偏好规划任务。例如用户记住「喜欢听周杰伦」，点播音乐时优先考虑。
```

### 判定: **GAP**

**影响**: 高风险 - 记忆功能失效
- P2 向量召回已工作，但 Planner 从未使用
- 用户偏好（如空调温度、音乐偏好）不会生效
- UI 可见 relevant_memories（`main.py:491, 549`），但云端规划未利用

**修复优先级**: P0（阻塞 P2 里程碑）

**修复建议**:
1. `planner.py:110-125` 注入 `context.memory_slice` 到 user_message
2. 系统提示添加指导：「结合用户记忆的偏好作为默认建议（用户当轮明确指令优先）」
3. 防御性检查：memory_slice 为空时不影响规划

---

## 其他发现：非硬约束但值得注意

### 8. 导航回家缺失地址时的交互

**当前行为** (`planner.py:230-238`):
```python
is_home_company = any(kw in user_utterance for kw in ["家", "回家", "到家", "家里", "公司", ...])
if is_home_company:
    response = "您还没有设置家/公司地址，请告诉我具体地址，比如：帮我记住家地址是望京SOHO"
else:
    response = "没找到这个目的地，可以说得更具体一些吗？"
```

**设计期望** (§9.1.5):
> 无记忆 → **追问**用户；不得静默落到 mock 地址

✓ **符合设计**，但可优化话术（当前已足够清晰）

---

### 9. 被动记忆评分公式对齐

**当前实现** (`p2_memory_service.py:210-216`):
```python
score = 0.4 * long_term_score + 0.3 * stability_score + 0.3 * personal_score
```

✓ **完全对齐 PRD v1.24 锁定公式**（§10.5）

---

### 10. 记忆黑名单覆盖

**当前实现** (`p2_memory_service.py:47-61`):
- 手机号 ✓
- 身份证号 ✓
- 银行卡号 ✓
- 密码/token ✓
- 病历关键词 ✓
- GPS 轨迹 ✓
- 对话原文检查 ✓

✓ **完全符合设计**（§10.4）

---

## 优先修复列表（按优先级排序）

### P0 - 阻塞 P2 里程碑

1. **[GAP-2] 被动记忆改为批抽架构**
   - **当前**: 每回合调用 `handle_passive_extraction`
   - **目标**: Task 终态写流水 → 批作业聚合（7天≥2次）
   - **工作量**: 2-3 天
   - **涉及文件**: 
     - 移除 `main.py:625-649`
     - 新增 `storage/models.py:TaskLedger` 表
     - 新增 `memory/batch_extractor.py` 批作业
     - 新增 cron 或 APScheduler 调度

2. **[GAP-2] 实现 dedupe_key 运行时生成**
   - **当前**: 无 dedupe_key 逻辑
   - **目标**: `pref:ac_temp:summer` 形式的键，避免重复记忆
   - **工作量**: 1 天
   - **涉及文件**: 
     - `memory/p2_memory_service.py:_generate_dedupe_key()`
     - `memory/batch_extractor.py` 聚合时使用

3. **[GAP-2] 实现季节化偏好**
   - **当前**: 无季节判断
   - **目标**: content 带「夏天空调偏好：26℃」，dedupe_key 带季节
   - **工作量**: 1 天
   - **涉及文件**: 
     - `memory/p2_memory_service.py:_get_season(user_timezone)`
     - `memory/active_memory_handler.py` 主动记忆格式化

4. **[GAP-7] Planner 注入记忆召回**
   - **当前**: `context.memory_slice` 未使用
   - **目标**: 注入 user_message 提示词
   - **工作量**: 0.5 天（简单但关键）
   - **涉及文件**: 
     - `planner/planner.py:110-125` 修改 user_message 构建

### P1 - 优化改进

5. **[PARTIAL-1] NavGoal 响应移除坐标字段**
   - **当前**: 家/公司返回 `latitude: 0.0, longitude: 0.0`
   - **目标**: 只返回 `address`，无坐标字段
   - **工作量**: 0.3 天
   - **涉及文件**: 
     - `adapters/navigation.py:119-140`
     - `schemas/taskgraph.py:NavGoal` 字段可选

---

## 测试覆盖建议

### 需新增测试

1. **P2 批抽流水**:
   - 同一偏好 7 天内 ≥2 次 → 写入长期库
   - 同一偏好 7 天内 <2 次 → 不写入
   - dedupe_key 冲突时更新 weight

2. **季节化偏好**:
   - 夏天记住「空调 26℃」→ dedupe_key: `pref:ac_temp:summer`
   - 冬天记住「空调 22℃」→ dedupe_key: `pref:ac_temp:winter`
   - 两条记忆不互相覆盖

3. **记忆注入规划**:
   - 用户记住「喜欢听周杰伦」
   - 说「放音乐」→ Planner 输出 `play_by_artist: 周杰伦`

4. **导航无地址追问**:
   - 首次「导航回家」且无记忆 → 追问地址
   - 说「帮我记住家地址是 xxx」→ 再次「回家」成功

---

## 结论

**当前实现质量**: 中等（4/7 正确，2 GAP，1 PARTIAL）

**架构符合度**: 60%
- 核心安全约束（L2、profile_ready）已正确实现
- 记忆架构存在设计偏离（被动提取、dedupe、召回注入）

**P2 里程碑阻塞项**: 2 个
- 被动批抽架构（GAP-2）
- 记忆注入规划（GAP-7）

**建议行动**:
1. **立即**: 修复 P0 阻塞项（预计 4-5 天）
2. **短期**: 优化 P1 改进项（预计 0.5 天）
3. **中期**: 补充测试覆盖（预计 2 天）

---

**审计员签名**: Cursor Agent  
**审计完成时间**: 2026-09-17 04:37 UTC
