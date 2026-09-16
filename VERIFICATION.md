# 验证修复的curl命令

## 前置条件
确保Docker stack正在运行：
```bash
docker-compose up -d
```

等待服务启动（约10秒）。

## A. 验证WebSocket连接修复

测试WebSocket端点是否可访问（不应返回403）：

```bash
# 使用wscat测试WebSocket连接（需要安装wscat: npm install -g wscat）
wscat -c "ws://localhost:8000/ws/test-session-123"

# 或者使用curl测试WebSocket升级请求（应返回101 Switching Protocols，不是403）
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: test" http://localhost:8000/ws/test-session-123
```

预期结果：
- 不应返回HTTP 403 Forbidden
- wscat应成功连接，Web UI不应显示"Disconnected"

## B. 验证向量搜索修复

### 1. 创建测试会话并记住家地址

```bash
# 1. 创建会话
SESSION_RESPONSE=$(curl -s -X POST http://localhost:8000/session/create \
  -H "Content-Type: application/json" \
  -d '{
    "driver_id": "test_driver_001",
    "vehicle_id": "test_vehicle_001"
  }')

SESSION_ID=$(echo $SESSION_RESPONSE | jq -r '.session_id')
echo "Session ID: $SESSION_ID"

# 2. 主动记忆：记住家地址
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"utterance\": \"记住我家在北京市朝阳区望京SOHO T1\"
  }" | jq '.taskgraph.tasks[0].steps[0].action.text'
```

预期结果：应返回类似"好的，已经帮您记住了"的确认消息。

### 2. 测试向量搜索（关键验证）

```bash
# 使用向量搜索查询记忆
curl -X POST http://localhost:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "account_default:test_driver_001",
    "query": "我家在哪里",
    "top_k": 5
  }' | jq '.'
```

**预期结果（修复前会失败，修复后应成功）：**
```json
{
  "user_id": "account_default:test_driver_001",
  "query": "我家在哪里",
  "count": 1,
  "memories": [
    {
      "memory_id": "...",
      "content": "家地址：北京市朝阳区望京SOHO T1",
      "category": "personal_basic",
      "weight": 1.0,
      "similarity": 0.xx,
      "score": 0.xx
    }
  ]
}
```

**修复前的错误（应该不再出现）：**
- `operator does not exist: vector <=> numeric[]`
- `count: 0, memories: []`

### 3. 测试导航回家功能

```bash
# 导航回家（应使用记忆中的地址）
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"utterance\": \"导航回家\"
  }" | jq '.taskgraph.tasks[0].steps[] | select(.domain == "NAVIGATION") | .action.goal'
```

预期结果：应返回包含 `address_text: "北京市朝阳区望京SOHO T1"` 的导航目标。

## C. 检查日志确认embedding dimensions修复

```bash
# 查看agent容器日志，确认没有dimensions相关错误
docker-compose logs agent | grep -i "dimension\|embedding"
```

预期结果：
- 应看到 `[QwenEmbedding] Initialized: model=text-embedding-v3, dim=1024`
- **不应**看到 `got an unexpected keyword argument 'dimensions'`
- **不应**看到 `Invalid embedding dimension: 0`

## D. 完整端到端测试脚本

```bash
#!/bin/bash
set -e

echo "=== 1. 创建会话 ==="
SESSION_RESPONSE=$(curl -s -X POST http://localhost:8000/session/create \
  -H "Content-Type: application/json" \
  -d '{"driver_id": "test_driver_e2e"}')
SESSION_ID=$(echo $SESSION_RESPONSE | jq -r '.session_id')
echo "Session ID: $SESSION_ID"

echo -e "\n=== 2. 记住家地址 ==="
curl -s -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION_ID\", \"utterance\": \"记住我家在上海市浦东新区\"}" \
  | jq -r '.taskgraph.tasks[0].steps[0].action.text'

sleep 2  # 等待后台被动提取完成

echo -e "\n=== 3. 向量搜索验证 ==="
SEARCH_RESULT=$(curl -s -X POST http://localhost:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "account_default:test_driver_e2e",
    "query": "家在哪",
    "top_k": 5
  }')

echo "$SEARCH_RESULT" | jq '.'

COUNT=$(echo "$SEARCH_RESULT" | jq -r '.count')
if [ "$COUNT" -gt 0 ]; then
  echo -e "\n✅ 向量搜索成功！找到 $COUNT 条记忆"
else
  echo -e "\n❌ 向量搜索失败！count = 0"
  exit 1
fi

echo -e "\n=== 4. 导航回家验证 ==="
curl -s -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION_ID\", \"utterance\": \"导航回家\"}" \
  | jq '.taskgraph.tasks[0].steps[] | select(.domain == "NAVIGATION") | .action.goal'

echo -e "\n✅ 所有测试通过！"
```

## 预期日志输出

运行测试后，检查容器日志：

```bash
docker-compose logs agent --tail=50
```

**应该看到（表示修复成功）：**
- `[QwenEmbedding] Initialized: model=text-embedding-v3, dim=1024`
- `[P2Memory] Saved: <memory_id> | personal_basic | 家地址：...`
- `[P2Memory] memory_search ... found_X` (X > 0)

**不应该看到（表示错误）：**
- `operator does not exist: vector <=> numeric[]`
- `got an unexpected keyword argument 'dimensions'`
- `Invalid embedding dimension: 0`
- `WebSocket connection failed: 403`

## 测试环境说明

如果需要在测试环境运行pytest（需要mock embedding API）：

```bash
# 在agent容器中运行
docker-compose exec agent bash
cd /app
export OPENAI_API_KEY=test  # 现在支持空key或'test'占位符

# 运行单元测试（会使用fallback逻辑，不实际调用embedding API）
pytest tests/test_p2_memory.py::TestSafetyGate -v
pytest tests/test_p2_memory.py::TestMemoryClassifier -v
pytest tests/test_p2_memory.py::TestPassiveExtractor -v
```

## 清理测试数据

```bash
# 清除测试用户的所有记忆
curl -X POST http://localhost:8000/memory/clear/account_default:test_driver_001
curl -X POST http://localhost:8000/memory/clear/account_default:test_driver_e2e
```
