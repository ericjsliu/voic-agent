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
┌─────────────────────────────────────────────────────────┐
│  🚗 Smart Cockpit Voice Agent         ● Connected       │
├───────────────────────┬─────────────────────────────────┤
│ Connection Settings   │                                 │
│ ┌───────────────────┐ │   TaskGraph / Writebacks /     │
│ │ Base URL          │ │      Telemetry Tabs            │
│ │ Driver ID         │ │                                 │
│ │ Model/Version     │ │   [JSON显示区域]                │
│ └───────────────────┘ │                                 │
│                       │                                 │
│ Chat Messages         │                                 │
│ ┌───────────────────┐ │                                 │
│ │ User: 打开车窗     │ │                                 │
│ │ Assistant: 好的... │ │                                 │
│ │ System: 需要确认... │ │                                 │
│ │   [确认] [取消]    │ │                                 │
│ └───────────────────┘ │                                 │
│                       │                                 │
│ [快捷短语chips]        │                                 │
│ [输入框] [发送]        │                                 │
└───────────────────────┴─────────────────────────────────┘
```

### 左侧面板

**Connection Settings（连接设置）**
- **Base URL**: Agent服务地址（默认 `http://localhost:8000`）
- **Driver ID**: 驾驶员ID（默认 `driver_001`）
- **Model**: 车型过滤（ModelA / ModelB / All）
- **Version**: 版本过滤（如 `2024`）
- **Create Session**: 创建新会话（首次使用必须点击）

**Chat Panel（聊天面板）**
- **用户消息**：蓝色气泡，右对齐
- **助手回复**：灰色气泡，左对齐
- **系统通知**：黄色边框，居中（如L2确认请求）
- **L2确认按钮**：高风险操作弹出 ✓确认 / ✕取消
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

**Tab 2: Writebacks**
- 车辆写回事件流
- 显示最近20条
- 颜色编码：
  - 🟢 `success` / `accepted` - 绿色
  - 🔴 `failed` / `rejected` / `declined` - 红色
  - 🔵 其他事件 - 蓝色
- 包含：task_id, step_id, event, status, reason, timestamp

**Tab 3: Telemetry**
- 车辆遥测数据（网格布局）
- 显示：
  - 档位 (Gear)
  - 车速 (Speed km/h)
  - 位置 (经纬度)
  - 车窗状态 (Windows)
  - 门锁状态 (Doors) - 🔒/🔓
  - 空调状态 (AC) - 开/关 + 温度

## 使用场景

### 场景1：测试多意图并行

1. 点击快捷短语 `打开车窗，同时播放音乐`
2. 观察右侧TaskGraph：应包含2个独立步骤
3. 观察Writebacks：两个步骤应并行执行

### 场景2：测试L2确认流程

1. 点击快捷短语 `锁车`
2. 聊天中出现黄色系统消息：`需要确认：锁车`
3. 出现两个按钮：✓确认 / ✕取消
4. 点击 ✓确认：执行锁车，Writeback显示 `accepted`
5. 点击 ✕取消：取消操作，Writeback显示 `declined`
6. 15秒不操作：自动超时，Writeback显示 `timeout`

### 场景3：测试RAG查询

1. 点击快捷短语 `如何使用空调`
2. 助手回复包含手册内容和引用
3. 右侧TaskGraph显示 `knowledge` 域
4. Writeback显示 `knowledge_done` 事件

### 场景4：测试导航

1. 点击快捷短语 `导航到机场`
2. POI自动解析为坐标
3. 右侧TaskGraph显示 `navigation` 域，包含 `goal` 坐标
4. Mock车辆模拟：
   - 2秒后：`nav_route_started`
   - 5秒后：`nav_arrived`

### 场景5：测试闲聊隔离

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
