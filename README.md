# Smart Cockpit Voice Dialogue Agent

智能座舱语音对话代理（云端侧）完整实现，采用架构路线C。

## 架构概览

```
ASR文本 → 上下文组装 → 规划器(LLM) → 编排器(状态机) → 领域适配器 → MQTT下行 → 车辆Mock → 写回
```

### 核心组件

1. **会话管理（Session Manager）**
   - 驾驶员切换时清空影子状态和记忆切片
   - 持久化会话信息

2. **上下文组装器（Context Assembler）**
   - 组装对话历史、影子状态、记忆切片、当前位置
   - 提供给规划器完整上下文

3. **规划器（Planner）**
   - LLM生成schema-constrained TaskGraph
   - LLM不可用时fallback到规则式规划
   - 预解析POI坐标

4. **编排器（Orchestrator）**
   - 自定义状态机，执行TaskGraph
   - L0：立即执行（查询类）
   - L1：立即执行，可撤销（控制类）
   - L2：需要确认才执行（15秒超时）
   - 支持并行执行独立步骤
   - 处理车辆写回（ack/confirm/nav事件）

5. **存储分层（Redis + PostgreSQL）**
   - **Redis（热路径）**: Session信息、影子状态、实体缓冲区（5分钟TTL）、最近对话、活跃任务、L2等待状态
   - **PostgreSQL（持久化）**: 长期记忆白名单（user_prefs, vehicle_config, frequent_destinations, music_prefs）、能力档案版本、用户手册元数据、TaskGraph审计日志、Writeback事件日志、时空事件摘要
   - **混合内存存储**: 白名单数据自动同步到PostgreSQL，其他热数据仅在Redis
   - **实体缓冲区**: 分钟级热缓存（最近POI、媒体、车辆对象、候选列表），在Context Assemble时注入，驾驶员切换时清空

6. **能力档案（Capability Profiles）**
   - 基于vehicle_model的动作白名单、参数范围、L1门控规则覆盖、功能特性标志
   - Model A (Standard): 基础功能（车窗、车门、空调）- 不支持天窗、座椅加热
   - Model B (Premium): 完整功能包括天窗、座椅加热、电动后备箱
   - 不支持的动作触发TTS"不支持"提示而非执行
   - 车辆连接时报告model_id → Session加载档案 → Planner过滤动作 → 仅支持的动作进入TaskGraph → Adapter验证 → MQTT发布
   
   **Profile切换门控（detailed-v1.5）**：
   - 车型/配置变更时进入`profile_switching`状态
   - **暂停所有MQTT下行执行边**，直到新Profile从PostgreSQL（source of truth）加载并绑定到session
   - 发出`profile_ready`通知后恢复MQTT下行
   - 云端**不猜测trim**，仅使用车辆报告的model+config
   - Web UI选择车型时触发相同的切换门控流程，显示switching/ready状态
   - 确保不会用旧whitelist执行新对话

7. **上下文增强（Context Features）**
   - **实体缓冲区**: 分钟级热数据（POI、媒体、车辆对象、候选列表），Context Assemble时注入
   - **改写/取消规则**: 30秒内同task_id可改写或取消（"算了"、"改成"关键词）
   - **拒绝规则**: 区域限制（学校/医院区域禁止车辆控制）、低置信度拒绝、侧聊检测（"今天天气"等不应触发车辆动作）
   - **知识引用强制**: 必须有citations[]才能作为手册权威回答，无引用则拒绝
   - **时空事件摘要**: 结构化事件存储（非原始对话），支持"昨天充电站"等查询

8. **领域适配器（Domain Adapters）**
   - **vehicle**: 车辆控制，验证档位/车速约束，能力档案验证，拒绝规则检查
   - **navigation**: 导航，POI解析，mock地图，记录时空事件
   - **media**: 媒体控制，实体缓冲区记录
   - **calendar**: 日历操作
   - **knowledge**: 混合RAG查询（仅文本，v1不支持视频），**强制citations[]**
   - **chitchat**: 闲聊，无schema/状态/记忆

9. **记忆存储（Memory Store）**
   - Redis + in-memory fallback
   - 白名单两阶段写入（user_prefs, vehicle_config, frequent_destinations, music_prefs）

8. **混合RAG客户端（Hybrid RAG Client）**
   - HTTP调用外部RAG服务
   - 支持车型/版本过滤
   - 返回带引用的结果

## MQTT主题

- **下行**：`cockpit/agent/taskgraph`（编排器验证后的TaskGraph）
- **上行**：
  - `cockpit/agent/writeback`（车辆写回）
  - `cockpit/agent/telemetry`（遥测数据）

**注意**：车辆只接收orchestrator验证后的TaskGraph，不会收到原始LLM草稿。

## 领域定义

| 域 | 动作示例 | 级别 | 备注 |
|---|---|---|---|
| vehicle | window_open/close, door_lock/unlock, ac_on/off/set_temp, sunroof_open/close, trunk_open | L1/L2 | door_lock/trunk_open是L2 |
| navigation | nav_to, nav_cancel | L0 | POI必须预解析 |
| media | play_music, pause, set_volume | L1 | |
| calendar | query_schedule, add_event | L0 | |
| knowledge | query_manual | L0 | 调用外部Hybrid RAG服务 |
| chitchat | response文本 | L0 | 无schema/状态/记忆 |

## 环境变量

```bash
# LLM配置 (Alibaba DashScope OpenAI-compatible)
# 获取API Key: https://dashscope.console.aliyun.com/
OPENAI_API_KEY=sk-your-dashscope-api-key-here
OPENAI_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_DEFAULT_MODEL=qwen-turbo

# Agent服务
AGENT_PORT=8000
MQTT_BROKER=localhost
MQTT_PORT=1883

# 存储配置（Redis热缓存 + PostgreSQL持久化）
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://cockpit:cockpit@localhost:5432/cockpit_agent

# Hybrid RAG服务
HYBRID_RAG_BASE_URL=http://localhost:8001
HYBRID_RAG_API_KEY=  # 可选

# 备注：
# - 如果不配置 OPENAI_API_KEY，系统会使用规则式规划fallback
# - 可用模型: qwen-turbo, qwen-plus, qwen-max, qwen3.8-27b 等
# - 本地开发: 复制 .env.example 为 .env 并填入真实API Key
# - Docker: 在 .env 文件中配置，docker-compose会自动读取
```

**⚠️ 安全提示**: 不要将真实的API Key提交到代码仓库！使用 `.env` 文件（已在 `.gitignore` 中）存储敏感信息。

## 快速启动

### 使用Docker Compose（一键启动）

```bash
# 1. 配置环境变量（首次运行）
cp .env.example .env
# 编辑 .env 文件，填入你的 Alibaba DashScope API Key

# 2. 启动所有服务（agent, mock_vehicle, mock_rag, mosquitto, redis, postgres, web）
docker-compose up --build

# 等待服务启动完成（约15秒）
# Agent: http://localhost:8000
# Mock RAG: http://localhost:8001
# MQTT: localhost:1883
# PostgreSQL: localhost:5432
# Web UI: http://localhost:3000

# 提示：如果没有配置 OPENAI_API_KEY，系统会自动使用规则式规划fallback
# PostgreSQL会自动创建表结构，无需手动迁移
```

### 本地开发模式

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入你的 Alibaba DashScope API Key

# 2. 安装依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 启动MQTT Broker（使用mosquitto或其他）
docker run -d -p 1883:1883 eclipse-mosquitto:2.0

# 4. 启动Redis（可选）
docker run -d -p 6379:6379 redis:7-alpine

# 5. 启动Mock RAG服务
python -m app.mock_rag.mock_rag_service

# 6. 启动Mock Vehicle
python -m app.vehicle_mock.mock_vehicle

# 7. 启动Agent (会自动读取 .env 文件)
python -m app.main
```

## Web UI 测试界面

### 快速体验

启动系统后，在浏览器打开 **http://localhost:3000** 即可使用交互式聊天界面：

**功能特性**：
- 💬 **聊天对话**：模拟语音输入，输入文本即可与智能座舱对话
- 🚗 **车型选择**：下拉菜单选择Model A/Model B，演示能力档案适配（不同车型支持不同动作）
- 🎯 **快捷短语**：预设demo场景一键发送（多意图、L2确认、RAG查询、闲聊）
- ✅ **L2确认**：高风险操作（锁车/开后备箱）弹出确认/取消按钮
- 📊 **实时面板**：
  - TaskGraph JSON可视化
  - Writeback事件流
  - 车辆遥测数据（档位、车速、锁状态、空调等）
- 🔌 **连接设置**：配置Agent URL、Driver ID、车辆Model、RAG车型/版本过滤
- 🌐 **WebSocket实时通信**：零延迟接收TaskGraph和Writeback

**Demo演示**：
1. **能力档案测试**：
   - 选择 "Model A (Standard)"，创建会话
   - 输入 `打开天窗` → 看到TTS提示"抱歉，您的车辆（Model A）不支持该功能：sunroof_open"
   - 选择 "Model B (Premium)"，创建新会话
   - 输入 `打开天窗` → 成功执行（Model B支持天窗）
2. 使用快捷短语或自定义输入测试其他场景：
   - **多意图**：`打开车窗，同时播放音乐` → 看右侧TaskGraph显示并行步骤
   - **L2确认**：`锁车` → 弹出确认按钮，15秒倒计时
   - **RAG查询**：`如何使用空调` → 看到带引用的知识回复
   - **导航**：`导航到机场` → POI自动解析为坐标
   - **闲聊**：`今天天气真好` → 系统识别为chitchat域

**截图展示**：

<details>
<summary>展开查看UI截图</summary>

![Web UI](docs/screenshot-web-ui.png)

深色座舱风格，左侧聊天对话，右侧实时数据面板。

</details>

---

## API演示（命令行）

### 1. 健康检查

```bash
curl http://localhost:8000/health
```

### 2. 创建会话

```bash
curl -X POST http://localhost:8000/session/create \
  -H "Content-Type: application/json" \
  -d '{"driver_id": "driver_001", "vehicle_id": "vehicle_001"}'
```

响应：
```json
{
  "session_id": "xxx-xxx-xxx",
  "driver_id": "driver_001",
  "vehicle_id": "vehicle_001",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### 3. 对话示例

#### 示例1：简单车辆控制（L1）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "打开车窗",
    "telemetry": {"gear": "P", "speed_kmh": 0}
  }'
```

响应：TaskGraph with vehicle.window_open action

#### 示例2：需要确认的动作（L2）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "锁车",
    "telemetry": {"gear": "P", "speed_kmh": 0}
  }'
```

响应：TaskGraph with vehicle.door_lock (L2), 等待用户确认

Mock Vehicle会在1秒后自动发送confirm_result=accepted

#### 示例3：导航

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "导航到机场",
    "telemetry": {"latitude": 39.9042, "longitude": 116.4074}
  }'
```

响应：TaskGraph with navigation.nav_to, POI已解析为坐标

Mock Vehicle会模拟：2秒后nav_route_started，5秒后nav_arrived

#### 示例4：知识查询（Hybrid RAG）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "如何使用空调",
    "telemetry": {"model": "ModelA", "version": "2024"}
  }'
```

响应：TaskGraph with knowledge.query_manual，包含RAG检索结果和引用

**RAG模型过滤测试**：

```bash
# 查询ModelA手册
curl -X POST http://localhost:8001/v1/hybrid-search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "空调",
    "filters": {"model": "ModelA", "version": "2024"},
    "top_k": 3
  }'

# 查询ModelB手册
curl -X POST http://localhost:8001/v1/hybrid-search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "空调",
    "filters": {"model": "ModelB", "version": "2024"},
    "top_k": 3
  }'
```

#### 示例5：多意图（并行执行）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "打开空调，同时播放音乐"
  }'
```

响应：TaskGraph包含2个独立步骤，orchestrator会并行执行

#### 示例6：闲聊（无schema/状态）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "xxx-xxx-xxx",
    "utterance": "今天天气真好"
  }'
```

响应：TaskGraph with chitchat domain，直接返回文本

## 测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_schemas.py
pytest tests/test_adapters.py
pytest tests/test_orchestrator.py

# 测试覆盖率
pytest --cov=app --cov-report=html
```

**注意**：RAG客户端测试需要mock RAG服务运行。

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI主应用
│   ├── schemas/                # JSON Schemas
│   │   ├── taskgraph.py
│   │   ├── writeback.py
│   │   └── context.py
│   ├── memory/                 # 记忆存储
│   │   └── store.py
│   ├── rag_client/             # RAG客户端
│   │   └── client.py
│   ├── adapters/               # 领域适配器
│   │   ├── base.py
│   │   ├── vehicle.py
│   │   ├── navigation.py
│   │   ├── media.py
│   │   ├── calendar.py
│   │   ├── knowledge.py
│   │   └── chitchat.py
│   ├── planner/                # 规划器
│   │   └── planner.py
│   ├── orchestrator/           # 编排器
│   │   └── orchestrator.py
│   ├── session/                # 会话管理
│   │   ├── session_manager.py
│   │   └── context_assembler.py
│   ├── vehicle_mock/           # Mock车辆
│   │   └── mock_vehicle.py
│   └── mock_rag/               # Mock RAG服务
│       └── mock_rag_service.py
├── tests/                      # 测试
│   ├── test_schemas.py
│   ├── test_adapters.py
│   ├── test_rag_client.py
│   └── test_orchestrator.py
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.rag
├── mosquitto.conf
├── pytest.ini
└── README.md
```

## 关键特性

### ✅ 已实现

- [x] 完整的架构路线C实现
- [x] L0/L1/L2动作级别控制
- [x] L2确认超时（15秒）
- [x] 并行执行独立步骤
- [x] 混合RAG with 车型/版本过滤
- [x] 知识查询强制引用（citations）
- [x] 闲聊无状态/无记忆隔离
- [x] 驾驶员切换清空状态
- [x] 记忆白名单两阶段写入
- [x] POI预解析（不下行未解析目的地）
- [x] Orchestrator验证后才下行
- [x] 规则式规划fallback（LLM不可用）
- [x] Mock车辆MQTT通信
- [x] Mock RAG服务（sample data for 2 models）
- [x] Docker Compose一键启动
- [x] 完整测试覆盖

### 架构约束

1. **下行安全**：只有orchestrator验证通过的TaskGraph才会下行到车辆，不会发送原始LLM草稿
2. **L2确认**：door_lock, trunk_open等高风险操作必须用户确认，15秒超时自动取消
3. **L1拒绝不重试**：车辆拒绝L1动作（如档位不在P）时，不会自动通过Planner重试
4. **Knowledge隔离**：v1仅支持文本RAG，不支持play_clip/video搜索
5. **Chitchat隔离**：无schema约束，无TaskState，不写入长期记忆
6. **POI必须解析**：导航目标必须在下行前解析为坐标，"home"等必须先映射
7. **引用强制**：知识查询结果必须包含citations，无命中结果时拒绝回答

## 故障排查

### MQTT连接失败

```bash
# 检查mosquitto是否运行
docker ps | grep mosquitto

# 查看日志
docker logs cockpit_mqtt
```

### RAG服务不可用

```bash
# 检查mock RAG服务
curl http://localhost:8001/health

# 查看日志
docker logs cockpit_mock_rag
```

### Agent启动失败

```bash
# 查看日志
docker logs cockpit_agent

# 检查环境变量
docker exec cockpit_agent env | grep -E "(MQTT|RAG|LLM)"
```

## 存储架构详解

### Redis vs PostgreSQL 职责划分

**Redis（热路径，短TTL）**:
```
- session:{session_id}          → 会话信息（24小时）
- shadow_state:{session_id}     → 影子状态（1小时）
- entity_buffer:{session_id}:*  → 实体缓冲区（5分钟）
- utterances:{session_id}       → 最近对话（24小时）
- active_task:{session_id}      → 活跃任务（30秒改写窗口）
```

**PostgreSQL（持久化，永久保存）**:
```
- long_term_memory              → 长期记忆白名单（driver级别）
  * user_prefs:driver_id:*
  * vehicle_config:driver_id:*
  * frequent_destinations:driver_id:*
  * music_prefs:driver_id:*

- capability_profile_versions   → 能力档案版本管理
- manual_metadata               → 用户手册元数据 + pgvector嵌入
- task_audit_logs               → TaskGraph执行审计
- writeback_logs                → Writeback事件历史
- spatiotemporal_events         → 时空事件摘要（非原始对话）
```

### 数据流

```
写入白名单数据（如user_prefs）:
  HybridMemoryStore.set_json() 
    → Redis（热缓存）
    → PostgreSQL（持久化）

读取白名单数据:
  HybridMemoryStore.get_json()
    → 先查Redis（快速）
    → Redis miss → 查PostgreSQL → 回填Redis

驾驶员切换:
  SessionManager.switch_driver()
    → 清空Redis: shadow_state, memory_slice, entity_buffer
    → 下次对话从PostgreSQL重新加载新驾驶员的whitelist
```

### 健康检查

```bash
curl http://localhost:8000/health
```

返回示例：
```json
{
  "status": "ok",
  "services": {
    "mqtt": true,
    "redis": true,
    "postgresql": true,
    "entity_buffer": true
  }
}
```

## 扩展指南

### 添加新领域

1. 在 `app/schemas/taskgraph.py` 添加新动作类型
2. 在 `app/adapters/` 创建新适配器
3. 在 `orchestrator.py` 注册适配器
4. 在 `planner.py` 添加规划规则
5. 添加测试

### 配置LLM模型

Alibaba DashScope 支持多种Qwen模型：

```bash
# 快速模型（推荐用于开发/测试）
LLM_DEFAULT_MODEL=qwen-turbo

# 更强大的模型
LLM_DEFAULT_MODEL=qwen-plus
LLM_DEFAULT_MODEL=qwen-max

# 特定版本
LLM_DEFAULT_MODEL=qwen3.8-27b
```

完整模型列表: https://help.aliyun.com/zh/dashscope/developer-reference/model-square

### 接入真实RAG服务

修改 `HYBRID_RAG_BASE_URL` 指向生产RAG服务：

```bash
HYBRID_RAG_BASE_URL=https://your-rag-service.com
HYBRID_RAG_API_KEY=your-api-key
```

确保RAG服务实现 `/v1/hybrid-search` 端点，遵循相同的请求/响应schema。

### 接入真实车辆

1. 替换 `app/vehicle_mock/` 为真实CAN总线/车辆API客户端
2. 实现真实的MQTT publisher/subscriber
3. 更新 `VehicleAdapter.execute()` 调用真实控制接口
4. 部署到车机或T-Box

## License

MIT

## 作者

Smart Cockpit Team
