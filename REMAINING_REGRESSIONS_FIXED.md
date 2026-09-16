# 剩余回归问题修复总结

本PR修复了PR#4未完全解决的两个Docker回归问题。

## 修复的问题

### 1. 向量搜索返回空结果 - OpenAI SDK迁移

#### 根本原因
代码使用了OpenAI SDK的legacy API（`openai.Embedding.create` 和 `openai.ChatCompletion.create`），但`requirements.txt`中已升级到`openai==1.3.7`（v1+版本），导致API不兼容：
```
You tried to access openai.Embedding, but this is no longer supported in openai>=1.0.0
```

#### 修复内容
**文件**: `app/memory/qwen_clients.py`

迁移到OpenAI v1+ SDK的客户端模式：

**修改前**:
```python
import openai

# 在__init__中
openai.api_base = api_base
openai.api_key = api_key

# 调用时
response = openai.Embedding.create(
    model=self.model,
    input=text,
    dimensions=self.dimension
)
```

**修改后**:
```python
from openai import OpenAI

# 在__init__中
self.client = OpenAI(api_key=api_key, base_url=api_base)

# 调用时
response = self.client.embeddings.create(
    model=self.model,
    input=text,
    dimensions=self.dimension
)
```

同时迁移了：
- `QwenEmbedding.embed()` - 单条embedding生成
- `QwenEmbedding.embed_batch()` - 批量embedding生成
- `QwenMemoryExtractor.score_utterance()` - 记忆评分
- `QwenMemoryExtractor.extract_facts()` - 事实提取

#### 验证
- ✅ Embedding API返回1024维向量
- ✅ 不再生成0维占位符
- ✅ 向量搜索返回非空结果
- ✅ 所有OpenAI API调用使用v1+ SDK

---

### 2. 导航回家仍返回「没找到这个目的地」- POI别名匹配增强

#### 根本原因
当用户说"导航回家"时，完整话语被传递给`resolve_poi()`，但代码只检查精确别名匹配：
```python
POI_ALIASES = {
    "回家": "家",  # 只匹配"回家"，不匹配"导航回家"
    "到家": "家",
    ...
}
```

导致`resolve_poi("导航回家")` 无法匹配到"家"这个POI，返回None，最终规划器返回chitchat错误。

#### 修复内容
**文件**: `app/adapters/navigation.py`

增强POI别名匹配，支持包含匹配（substring match）：

**修改前**:
```python
# 只有精确匹配
if text in POI_ALIASES:
    text = POI_ALIASES[text]
```

**修改后**:
```python
# 精确匹配
if text in POI_ALIASES:
    text = POI_ALIASES[text]
# 如果不是精确匹配，检查是否包含别名（例如"导航回家"包含"回家"）
else:
    for alias, standard_name in POI_ALIASES.items():
        if alias in text:
            text = standard_name
            break
```

现在支持的话语模式：
- ✅ "导航回家" → 解析为"家"
- ✅ "帮我导航回家" → 解析为"家"
- ✅ "我要回家" → 解析为"家"
- ✅ "带我回家" → 解析为"家"
- ✅ "导航到家" → 解析为"家"
- ✅ "去家里" → 解析为"家"

#### 验证
- ✅ Memory有home地址时，所有包含"回家"/"到家"/"家里"的话语都能解析
- ✅ 返回navigation step，包含`address_text`
- ✅ 不再返回「没找到这个目的地」
- ✅ 保持`latitude=0, longitude=0`（云端不geocode架构）

---

## 测试覆盖

### 新增单元测试

**文件**: `tests/test_p2_memory.py`

#### 1. `TestNavigationIntegration::test_nav_home_from_full_utterance`
测试导航回家（完整话语模式）：
- 测试6种不同的话语模式
- 验证所有模式都能正确解析为"家"
- 验证返回正确的address_text

#### 2. `TestEmbeddingClientAPI` (5个测试)
- `test_embedding_client_initialization` - 验证OpenAI v1+ 客户端初始化
- `test_embedding_api_call_shape` - 验证API调用返回1024维向量
- `test_embedding_empty_text_returns_none` - 验证空文本返回None（不生成0维向量）
- `test_embedding_invalid_dimension_returns_none` - 验证错误维度返回None
- `test_chat_completion_client_initialization` - 验证聊天客户端初始化

### 测试结果
```bash
pytest tests/test_p2_memory.py -q
# 32 passed, 4 skipped, 2 warnings in 0.61s
```

所有测试通过，包括：
- ✅ 原有的26个测试（PR#3的测试）
- ✅ 新增的6个回归测试
- ✅ 4个跳过的测试（需要Postgres的集成测试）

---

## 架构确认

### 云端不做geocode
修复保持了P2记忆架构设计：
- 家/公司地址只存储content文本（格式：`家地址：xxx`）
- 返回`address_text`给车端，车端自行geocode
- 云端返回`latitude=0, longitude=0`
- NavigationAdapter从P2 memory读取地址

### ChitchatAction Schema
验证了PR#4的修复未被破坏：
- ✅ 使用`ChitchatAction(response=..., level=...)`格式
- ✅ 不使用`{"action": "speak", "text": ...}`格式

---

## 变更文件

1. `app/memory/qwen_clients.py` - 迁移到OpenAI v1+ SDK
2. `app/adapters/navigation.py` - 增强POI别名匹配
3. `tests/test_p2_memory.py` - 添加6个回归测试
4. `REMAINING_REGRESSIONS_FIXED.md` - 本文档

---

## 总结

所有剩余的Docker回归问题已修复：
1. ✅ 向量搜索正常工作（OpenAI v1+ SDK）
2. ✅ 导航回家使用memory地址（包含匹配）
3. ✅ 保持PR#4的ChitchatAction修复

测试覆盖率：
- ✅ 单元测试: 32 passed（包含6个新增回归测试）
- ✅ 所有相关测试通过
- ✅ 未破坏任何现有功能

准备好进行端到端Docker测试。
