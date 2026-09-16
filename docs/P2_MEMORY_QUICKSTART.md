# P2 长期记忆系统 - 快速开始指南

## 前置要求

1. PostgreSQL 数据库（支持 pgvector 扩展）
2. Python 3.9+
3. Redis（可选，用于热缓存）

## 安装步骤

### 1. 安装 pgvector 扩展

```bash
# Ubuntu/Debian
sudo apt install postgresql-15-pgvector

# macOS
brew install pgvector

# 或从源码编译
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
```

### 2. 启用扩展并运行迁移

```bash
# 连接数据库
psql -U cockpit -d cockpit_agent

# 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;

# 退出psql
\q

# 运行迁移脚本
python migrations/001_create_p2_memory_table.py
```

### 3. 验证安装

```bash
# 检查表是否创建
psql -U cockpit -d cockpit_agent -c "\d long_term_memory_p2"

# 应该看到9个字段：
# - memory_id (character varying)
# - user_id (character varying)
# - content (text)
# - category (character varying)
# - embedding (vector(1024))
# - weight (double precision)
# - version_id (integer)
# - created_at (timestamp)
# - updated_at (timestamp)
# - source_ref (character varying)
```

### 4. 启动应用

```bash
# 使用 docker-compose
docker-compose up

# 或本地运行
python -m app.main
```

## 快速测试

### 1. 测试主动记忆（"帮我记"）

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "utterance": "帮我记住我家在北京市朝阳区望京SOHO",
    "driver_id": "drv_test_001"
  }'

# 应该返回：{"好的，已记住这个地址"}
```

### 2. 查看记忆

```bash
curl -X POST http://localhost:8000/memory/list \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "account_default:drv_test_001",
    "limit": 10
  }'

# 应该看到记忆列表，包含刚才记录的家地址
```

### 3. 测试导航回家

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "utterance": "导航回家",
    "driver_id": "drv_test_001"
  }'

# 应该生成导航TaskGraph，目的地解析为家地址
```

### 4. 测试被动提取

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "utterance": "我平时喜欢听周杰伦的歌",
    "driver_id": "drv_test_001"
  }'

# 对话完成后，被动提取会在后台运行
# 几秒后查看记忆列表，应该看到音乐偏好
```

### 5. 测试PII黑名单

```bash
curl -X POST http://localhost:8000/dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "utterance": "帮我记住我的手机号是13812345678",
    "driver_id": "drv_test_001"
  }'

# 应该被阻止，查看日志会显示 BLOCK: contains_phone
```

### 6. 运行单元测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio -i https://pypi.tuna.tsinghua.edu.cn/simple

# 创建测试数据库
createdb -U cockpit cockpit_agent_test

# 运行测试
pytest tests/test_p2_memory.py -v

# 应该看到所有测试通过：
# - test_block_phone_number: PASSED
# - test_classify_home_address: PASSED
# - test_put_memory_home_address: PASSED
# - test_block_pii: PASSED
# - test_passive_preference_extraction: PASSED
# - test_driver_isolation: PASSED
# - test_nav_home_from_memory: PASSED
```

## 功能验证清单

- [ ] 主动记忆：帮我记 → 直接写入（无确认）
- [ ] 被动提取：对话后自动提取偏好
- [ ] PII黑名单：手机号/身份证被阻止
- [ ] 家/公司地址：仅存content（无lat/lng）
- [ ] 导航回家：从memory解析地址 → resolve POI
- [ ] 驾驶员隔离：不同driver_id的记忆互不干扰
- [ ] 向量召回：搜索相关记忆（需真实embedding API）
- [ ] API端点：list/search/delete/clear/opt_out

## 常见问题

### Q1: 迁移失败 "pgvector extension not found"

**A:** 确保安装了 pgvector 扩展：
```bash
sudo apt install postgresql-15-pgvector
# 或从源码编译
```

### Q2: embedding 总是返回伪向量

**A:** 当前实现使用占位。需要替换 `app/memory/p2_memory_service.py` 中的 `_generate_embedding()` 方法：

```python
def _generate_embedding(self, text: str) -> Optional[List[float]]:
    # TODO: 替换为真实API
    import openai
    response = openai.Embedding.create(
        model="text-embedding-v3",
        input=text
    )
    return response['data'][0]['embedding']
```

### Q3: 向量召回失败

**A:** 检查：
1. pgvector扩展是否启用
2. embedding列是否有NULL值
3. 索引是否创建（`\di long_term_memory_p2*`）

### Q4: 记忆被丢弃（无法分类）

**A:** 调整 `MemoryClassifier` 关键词，或改用LLM分类。

### Q5: 导航回家无法解析地址

**A:** 检查：
1. 家地址是否已记录（`/memory/home_company/{user_id}`）
2. 地址格式是否为 "家地址：xxx"
3. geocoding是否正常（当前为mock）

## 性能调优

### 向量索引优化

数据量 >10k 后：

```sql
-- 调整 lists 参数（推荐 sqrt(row_count)）
DROP INDEX idx_p2_memory_embedding_ivfflat;
CREATE INDEX idx_p2_memory_embedding_ivfflat
ON long_term_memory_p2
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);  -- 根据数据量调整
```

### 权重衰减

定期任务（每周）：

```sql
UPDATE long_term_memory_p2
SET weight = weight * 0.95
WHERE updated_at < NOW() - INTERVAL '7 days';
```

### 清理低权重记忆

```sql
DELETE FROM long_term_memory_p2
WHERE weight < 0.1 AND updated_at < NOW() - INTERVAL '30 days';
```

## 下一步

1. **替换embedding占位**: 接入真实API（DashScope、OpenAI等）
2. **优化分类器**: 使用LLM分类提升准确率
3. **接入地图服务**: 替换geocoding mock
4. **监控与告警**: 添加Prometheus metrics
5. **用户界面**: 记忆管理UI（查看/删除/关闭）

## 文档

- 完整实现文档：[docs/P2_MEMORY_IMPLEMENTATION.md](docs/P2_MEMORY_IMPLEMENTATION.md)
- 技术方案：[uploads/cockpit-voice-agent-tech-design-detailed.md](uploads/cockpit-voice-agent-tech-design-detailed.md) §11.13
- API文档：见 [docs/P2_MEMORY_IMPLEMENTATION.md](docs/P2_MEMORY_IMPLEMENTATION.md) API端点章节

## 支持

如有问题，请：
1. 检查日志（`[P2Memory]` 前缀）
2. 查看审计事件（`audit_event_logs` 表）
3. 运行测试验证功能

---

**版本**: P2 Full  
**状态**: 生产就绪（需替换embedding占位）  
**对齐**: PRD v1.22 + 详细技术方案 v2.8.1 §11.13
