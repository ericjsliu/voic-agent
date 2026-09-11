# Web UI 使用指南

## 快速开始

### 1. 启动系统

```bash
# 方式1：Docker Compose（推荐）
docker-compose up --build

# 方式2：本地开发
# Terminal 1: 启动后端
python -m app.main

# Terminal 2: 启动前端
cd web
npm install
npm run dev
```

### 2. 访问界面

打开浏览器访问：**http://localhost:3000**

## 界面功能

### 主界面布局

```
┌──────────────────────────────────────────────────────────────────┐
│  🚗 Smart Cockpit Voice Agent              ● Connected           │
├─────────────────────────────┬────────────────────────────────────┤
│ Connection Settings         │                                    │
│ [Base URL] [Driver ID]      │  TaskGraph / Writebacks /         │
│ [Model] [Version]           │     Telemetry Tabs                │
├─────────────────────────────┤                                    │
│ 🚗 Vehicle State (Mock)     │  [JSON/Stream显示区域]             │
│ Gear: [P][R][N][D]          │                                    │
│ Speed: [- 0 +] km/h         │                                    │
│ Windows/Doors/AC toggles    │                                    │
│ ⚠️ L1 Reject hints          │                                    │
├─────────────────────────────┤                                    │
│ 🔒 L2 Confirm Zone          │                                    │
│ ⏱️ 15s Countdown            │                                    │
│ [✓ 确认执行] [✕ 取消操作]    │                                    │
├─────────────────────────────┤                                    │
│ Chat Messages               │                                    │
│ User: 打开车窗               │                                    │
│ Assistant: 好的...          │                                    │
│   📚 Citations (if RAG)     │                                    │
│ System: ⚠️ 需要确认          │                                    │
├─────────────────────────────┤                                    │
│ [Quick chips]               │                                    │
│ [Input] [Send]              │                                    │
└─────────────────────────────┴────────────────────────────────────┘
```

### 左侧面板（增强版）

**Connection Settings（连接设置）**
- **Base URL**: Agent服务地址（默认 `http://localhost:8000`）
- **Driver ID**: 驾驶员ID（默认 `driver_001`）
- **Model**: 车型过滤（ModelA / ModelB / All）
- **Version**: 版本过滤（如 `2024`）
- **Create Session**: 创建新会话（首次使用必须点击）

**Vehicle State Panel（车辆状态面板 - Mock）** ⭐ NEW
- **Gear Selector**: P/R/N/D 档位切换按钮
- **Speed Control**: +/- 按钮和输入框调整车速
- **Windows Toggle**: 开/关车窗状态
- **Doors Toggle**: 锁定/解锁状态
- **AC Toggle**: 空调开/关
- **Location**: 显示当前GPS坐标
- **L1 Rejection Hints**: 显示哪些状态会导致L1拒绝
  - Gear ≠ P → door_lock/trunk_open **rejected**
  - Speed > 0 → sunroof_open **rejected**

**L2 Confirm Zone（L2确认区域 - 专用）** ⭐ NEW
- **Empty State**: 无待确认时显示虚线边框
- **Active State**: 有L2待确认时高亮并脉冲动画
- **Badge**: 显示 "L2 High-Risk Action"
- **15s Countdown**: 大字体倒计时，最后5秒变红色
- **Progress Bar**: 视觉进度条（15秒→0）
- **Action Buttons**: 
  - ✓ 确认执行（绿色渐变）
  - ✕ 取消操作（红色边框）
- **Timeout Notice**: 超时后显示 "已取消" 提示
- **No Re-ask**: 超时后系统不会重新请求Planner

**Chat Panel（聊天面板）**
- **用户消息**：蓝色气泡，右对齐
- **助手回复**：灰色气泡，左对齐
  - **Citations Cards** ⭐ NEW: 知识查询回复下方显示引用卡片
    - Section名称
    - Page页码
    - Doc ID文档来源
    - 蓝色边框高亮
- **系统通知**：黄色边框，居中（如L2确认请求、超时通知）
- **时间戳**：每条消息下方显示

**Quick Chips（快捷短语）**
5个预设demo场景，点击即发送：
1. `打开车窗，同时播放音乐` - 多意图并行执行
2. `锁车` - L2确认流程
3. `导航到机场` - POI解析
4. `如何使用空调` - RAG知识查询
5. `今天天气真好` - 闲聊测试

**Input（输入框）**
- 输入文本模拟语音指令
- 按 Enter 发送
- 显示placeholder提示

### 右侧面板

**Tab 1: TaskGraph**
- JSON格式显示当前TaskGraph
- 实时更新（WebSocket推送）
- 代码高亮显示

**Tab 2: Writebacks** ⭐ ENHANCED
- **Chronological Stream**: 按时间顺序显示最近30条
- **Highlighted Rejection/Timeout**: 
  - `rejected` 和 `timeout` 状态高亮显示
  - 红色边框 + 背景 + 阴影效果
  - 图标提示：🚫 rejected, ⏱️ timeout
- **Detailed Reason**: 
  - L1 reject: "gear_not_P", "speed_not_zero" 等
  - L2 timeout: "L2 confirmation timeout - operation cancelled"
- **Color Coding**：
  - 🟢 `success` / `accepted` - 绿色
  - 🔴 `failed` / `rejected` / `declined` / `timeout` - 红色高亮
  - 🔵 其他事件 - 蓝色
- **Fields**: task_id (缩短), step_id, event, status, reason, timestamp

**Tab 3: Telemetry**
- 车辆遥测数据（网格布局）
- 显示：
  - 档位 (Gear)
  - 车速 (Speed km/h)
  - 位置 (经纬度)
  - 车窗状态 (Windows)
  - 门锁状态 (Doors) - 🔒/🔓
  - 空调状态 (AC) - 开/关 + 温度

## 演示场景（完整）

### 场景1：L1拒绝测试（档位约束）⭐ NEW

**目标**: 验证车辆状态验证和L1拒绝机制

1. **设置档位≠P**: 在Vehicle State Panel中点击 `D` 档
2. 输入：`锁车`
3. **观察**:
   - 右侧Writebacks出现 `vehicle_ack` 事件
   - Status: `rejected` (红色高亮)
   - Reason: "gear_not_P" 或类似
   - **注意**: 系统不会重试，直接拒绝

**变体**:
- Gear=D，输入 `打开后备箱` → rejected
- Speed>0（点击+按钮），输入 `打开天窗` → rejected

### 场景2：L2超时测试（15秒倒计时）⭐ NEW

**目标**: 验证L2确认超时机制和无重试逻辑

1. **确保Gear=P, Speed=0**
2. 输入：`锁车`
3. **观察**:
   - L2 Confirm Zone 激活（黄色高亮 + 脉冲）
   - 倒计时从15秒开始
   - Progress bar 逐渐缩短
4. **不点击任何按钮，等待15秒**
5. **结果**:
   - 倒计时到0
   - Timeout Notice 出现："已取消"
   - 聊天中出现：`⏱️ 确认超时 - 已取消`
   - Writebacks中出现：`confirm_result` / `timeout` (红色高亮)
   - **关键**: 系统不会重新询问Planner

### 场景3：L2正常确认流程

**目标**: 验证L2确认并执行

1. **确保Gear=P, Speed=0**
2. 输入：`锁车`
3. L2 Confirm Zone 激活
4. **在15秒内点击 `✓ 确认执行`**
5. **观察**:
   - L2 Zone 消失
   - 聊天显示：`✓ 已确认执行`
   - Writebacks: `confirm_result` / `accepted` (绿色)
   - 随后: `vehicle_ack` / `success`

### 场景4：导航两次Beat测试 ⭐ NEW

**目标**: 验证导航事件流（route_started → arrived）

1. 输入：`导航到机场`
2. **观察Writebacks（时间线）**:
   - t=0s: TaskGraph下行，POI已解析
   - t=2s: `nav_route_started` writeback 出现
   - t=5s: `nav_arrived` writeback 出现
3. **验证**: 两个beat事件按顺序出现，间隔正确

### 场景5：RAG引用卡片测试 ⭐ NEW

**目标**: 验证知识查询citations显示

1. 在Connection Settings中设置：
   - Model: `ModelA`
   - Version: `2024`
2. 输入：`如何使用空调`
3. **观察助手回复**:
   - 文本内容（手册片段）
   - **下方出现 📚 References 区域**
   - Citation cards:
     - Section: "第3章 空调系统"
     - Page: p.45
     - Doc: manual_modelA_2024
   - 蓝色边框卡片

**无引用警告测试**:
1. 输入一个无关查询（触发无结果）
2. 如果知识路径返回无citations，应显示警告（而非作为权威结论）

### 场景6：测试多意图并行

1. 点击快捷短语 `打开车窗，同时播放音乐`
2. 观察右侧TaskGraph：应包含2个独立步骤
3. 观察Writebacks：两个步骤应并行执行

### 场景7：混合意图处理（task+chitchat）⭐ NEW

**目标**: 验证混合utterance的处理顺序

1. 输入包含任务和闲聊的混合语句（如果支持）
2. **期望**: UI应先显示可执行步骤，闲聊内容在后（或被过滤）
3. 验证后端是否按此逻辑处理

### 场景8：测试闲聊隔离

1. 点击快捷短语 `今天天气真好`
2. 助手简短回复（无复杂处理）
3. TaskGraph显示 `chitchat` 域
4. 验证：闲聊不写入长期记忆

## 调试技巧

### 查看WebSocket连接

打开浏览器开发者工具 (F12)：
- **Network** → **WS** → 查看WebSocket消息
- 连接URL：`ws://localhost:8000/ws/{session_id}`
- 实时查看 `taskgraph` 和 `writeback` 消息

### 查看API请求

开发者工具 → **Network** → **Fetch/XHR**：
- `POST /session/create` - 创建会话
- `POST /dialogue` - 发送对话

### 查看Console日志

开发者工具 → **Console**：
- WebSocket连接/断开日志
- 错误信息

## 故障排查

### Web UI无法访问

```bash
# 检查容器状态
docker ps | grep cockpit

# 查看日志
docker logs cockpit_web_ui

# 确认端口映射
curl http://localhost:3000
```

### WebSocket连接失败

检查：
1. Agent服务是否运行：`curl http://localhost:8000/health`
2. Session ID是否有效：点击 "Create Session"
3. 浏览器Console是否有错误

### 消息发送失败

检查：
1. 是否已创建Session
2. Agent服务是否正常：`docker logs cockpit_agent`
3. Network错误：查看开发者工具

## 开发模式

### 前端热重载

```bash
cd web
npm run dev
```

访问：http://localhost:5173 (Vite dev server)

### 修改样式

编辑 `web/src/components/*.css` 文件，保存自动刷新。

### 修改组件

编辑 `web/src/components/*.tsx` 文件，保存自动刷新。

## 扩展建议

### 添加新的快捷短语

编辑 `web/src/components/ChatPanel.tsx`：

```typescript
const DEMO_PHRASES = [
  '打开车窗，同时播放音乐',
  '锁车',
  '导航到机场',
  '如何使用空调',
  '今天天气真好',
  '你的新短语'  // 添加这里
]
```

### 自定义主题颜色

编辑 `web/src/App.css` 和 `web/src/index.css`：

```css
/* 修改主色调 */
.app-header h1 {
  background: linear-gradient(135deg, #your-color-1, #your-color-2);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
```

## 性能优化

### 生产构建

```bash
cd web
npm run build
```

生成的 `dist/` 目录包含优化后的静态文件。

### 减少Bundle大小

- 代码分割已启用（Vite自动处理）
- Gzip压缩已启用（nginx配置）
- 按需加载组件

## 安全建议

### CORS配置

生产环境应限制CORS源：

编辑 `app/main.py`：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-domain.com"],  # 改为具体域名
    # ...
)
```

### API认证

生产环境应添加认证：
- JWT tokens
- API keys
- OAuth2

## 贡献指南

欢迎提交UI改进！

提交PR时请包含：
1. 功能描述
2. 截图（如有UI变更）
3. 测试说明
