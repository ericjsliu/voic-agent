# -*- coding: utf-8 -*-
"""Qwen模型客户端（记忆提取和embedding）

使用DashScope OpenAI-compatible API:
- MEMORY_EXTRACT_MODEL (default: qwen-turbo) - 被动评分 + 事实提取 + 10类分类
- MEMORY_EMBED_MODEL (default: text-embedding-v3) - 向量embedding (1536维)
"""

import os
import json
from typing import Optional, Dict, List, Any
from openai import OpenAI


class QwenMemoryExtractor:
    """Qwen记忆提取客户端
    
    使用小模型（qwen-turbo或qwen-plus）进行：
    1. 被动评分（PRD v1.24加权公式）
    2. 事实提取
    3. 10类分类
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 5.0
    ):
        """
        Args:
            model: 模型名称（默认从MEMORY_EXTRACT_MODEL env读取，fallback: qwen-turbo）
            api_base: API基础URL（默认从OPENAI_API_BASE读取）
            api_key: API密钥（默认从OPENAI_API_KEY读取）
            timeout: 超时时间（秒）
        """
        self.model = model or os.getenv('MEMORY_EXTRACT_MODEL', 'qwen-turbo')
        self.api_base = api_base or os.getenv('OPENAI_API_BASE', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.api_key = api_key or os.getenv('OPENAI_API_KEY', '')
        self.timeout = timeout
        
        # 创建OpenAI v1+ 客户端
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        
        print(f"[QwenMemoryExtractor] Initialized: model={self.model}, base={self.api_base}")
    
    def score_utterance(
        self,
        utterance: str,
        assistant_response: str,
        timeout: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """评分用户话语（PRD v1.24加权公式）
        
        Args:
            utterance: 用户话语
            assistant_response: 助手回复
            timeout: 超时时间（秒）
        
        Returns:
            {
                'long_term': 0.0-1.0,
                'stability': 0.0-1.0,
                'personal': 0.0-1.0,
                'score': 0.0-1.0,  # 0.4*long + 0.3*stability + 0.3*personal
            }
            失败返回None（不阻塞主流程）
        """
        timeout = timeout or self.timeout
        
        system_prompt = """你是记忆评分助手。评估用户话语的记忆价值，输出JSON：
{
  "long_term": 0.0-1.0,     // 长期性：不是一次性临时信息
  "stability": 0.0-1.0,     // 稳定性：不是情绪性或单次事实
  "personal": 0.0-1.0,      // 个人属性：关于用户自己
  "score": 0.0-1.0          // 加权：0.4*long_term + 0.3*stability + 0.3*personal
}

低长期性（应跳过）：
- 一次性交通查询（堵车吗、路况）
- 下个路口类问题
- 临时车辆状态查询（当前车速、现在温度）

高长期性：我、我的、家、公司、喜欢、习惯、偏好、经常、总是、平时
高稳定性：总是、一直、经常、平时、习惯
高个人属性：我、我的、我家

只输出JSON，不要解释。"""
        
        user_prompt = f"用户话语：{utterance}"
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},  # 强制JSON输出
                timeout=timeout
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            # 验证字段
            if all(k in result for k in ['long_term', 'stability', 'personal', 'score']):
                return result
            else:
                print(f"[QwenMemoryExtractor] Invalid response format: {result}")
                return None
        
        except Exception as e:
            print(f"[QwenMemoryExtractor] Scoring failed: {e}")
            return None
    
    def extract_facts(
        self,
        utterance: str,
        assistant_response: str,
        timeout: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """提取事实并分类
        
        Args:
            utterance: 用户话语
            assistant_response: 助手回复
            timeout: 超时时间（秒）
        
        Returns:
            {
                'facts': ['归一化事实1', '归一化事实2'],
                'category': '10类之一',
            }
            失败返回None（不阻塞主流程）
        """
        timeout = timeout or self.timeout
        
        system_prompt = """你是记忆提取助手。从用户话语中提取归一化事实，并分类到10类之一。输出JSON：
{
  "facts": ["事实1", "事实2"],
  "category": "10类之一"
}

10类可写：
1. personal_basic - 个人基础信息（家/公司地址、称呼）
2. personal_background - 个人背景（职业、教育）
3. user_preference - 用户偏好（空调温度、音乐）
4. relationships - 人物关系（家人、朋友）
5. goals_plans - 目标与计划
6. tasks_agreements - 任务与约定
7. knowledge_experience - 知识与经验
8. restrictions - 限制与禁忌
9. health_habits - 个人健康习惯（非病历）
10. items_devices - 物品与设备

归一化规则：
- "我家在北京市朝阳区" -> "家地址：北京市朝阳区"
- "公司在海淀区" -> "公司地址：海淀区"
- "我喜欢听周杰伦" -> "喜欢听周杰伦"
- "空调温度22度" -> "偏好空调温度：22度"

只输出JSON，不要解释。"""
        
        user_prompt = f"用户话语：{utterance}"
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=timeout
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            # 验证字段
            if 'facts' in result and 'category' in result:
                return result
            else:
                print(f"[QwenMemoryExtractor] Invalid extraction format: {result}")
                return None
        
        except Exception as e:
            print(f"[QwenMemoryExtractor] Extraction failed: {e}")
            return None


class QwenEmbedding:
    """Qwen Embedding客户端
    
    使用DashScope text-embedding-v3（1024维，用户锁定）
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0
    ):
        """
        Args:
            model: 模型名称（默认从MEMORY_EMBED_MODEL env读取，fallback: text-embedding-v3）
            api_base: API基础URL（默认从OPENAI_API_BASE读取）
            api_key: API密钥（默认从OPENAI_API_KEY读取）
            timeout: 超时时间（秒）
        """
        self.model = model or os.getenv('MEMORY_EMBED_MODEL', 'text-embedding-v3')
        self.api_base = api_base or os.getenv('OPENAI_API_BASE', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.api_key = api_key or os.getenv('OPENAI_API_KEY', '')
        self.timeout = timeout
        # 用户锁定：text-embedding-v3 输出1024维
        self.dimension = int(os.getenv('EMBEDDING_DIMENSIONS', '1024'))
        
        # 创建OpenAI v1+ 客户端
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        
        print(f"[QwenEmbedding] Initialized: model={self.model}, dim={self.dimension}, base={self.api_base}")
    
    def embed(
        self,
        text: str,
        timeout: Optional[float] = None
    ) -> Optional[List[float]]:
        """生成文本embedding
        
        Args:
            text: 输入文本
            timeout: 超时时间（秒）
        
        Returns:
            1024维向量，失败返回None（绝不返回0维占位符）
        """
        timeout = timeout or self.timeout
        
        if not text or len(text.strip()) == 0:
            print(f"[QwenEmbedding] ERROR: Empty text, cannot generate embedding")
            return None
        
        try:
            # DashScope text-embedding-v3 默认1024维，需要在parameters中指定
            # 注意：DashScope的OpenAI兼容模式可能不支持dimensions参数，需要在extra_body中传递
            try:
                # 尝试使用dimensions参数（OpenAI标准）
                response = self.client.embeddings.create(
                    model=self.model,
                    input=text,
                    dimensions=self.dimension,
                    timeout=timeout
                )
            except Exception as e1:
                # 如果不支持dimensions参数，尝试不传（text-embedding-v3默认1024维）
                print(f"[QwenEmbedding] Dimensions param not supported, using default: {e1}")
                response = self.client.embeddings.create(
                    model=self.model,
                    input=text,
                    timeout=timeout
                )
            
            if not response or not response.data or len(response.data) == 0:
                print(f"[QwenEmbedding] ERROR: Empty response from API")
                return None
            
            embedding = response.data[0].embedding
            
            # 验证维度（必须匹配用户锁定值）
            if not embedding or len(embedding) == 0:
                print(f"[QwenEmbedding] ERROR: Got empty embedding vector")
                return None
            
            if len(embedding) != self.dimension:
                print(f"[QwenEmbedding] ERROR: Expected {self.dimension} dims, got {len(embedding)}")
                return None  # 失败返回None，绝不写0维占位符
            
            return embedding
        
        except Exception as e:
            print(f"[QwenEmbedding] Embedding failed: {e}")
            return None  # 失败返回None，不阻塞主流程
    
    def embed_batch(
        self,
        texts: List[str],
        timeout: Optional[float] = None
    ) -> List[Optional[List[float]]]:
        """批量生成embedding
        
        Args:
            texts: 输入文本列表
            timeout: 超时时间（秒）
        
        Returns:
            向量列表，失败的条目为None（绝不返回0维占位符）
        """
        timeout = timeout or self.timeout
        
        if not texts or len(texts) == 0:
            return []
        
        # 过滤空文本
        valid_texts = [t for t in texts if t and len(t.strip()) > 0]
        if len(valid_texts) != len(texts):
            print(f"[QwenEmbedding] WARNING: Filtered {len(texts) - len(valid_texts)} empty texts")
        
        try:
            # DashScope text-embedding-v3 默认1024维
            try:
                # 尝试使用dimensions参数
                response = self.client.embeddings.create(
                    model=self.model,
                    input=valid_texts,
                    dimensions=self.dimension,
                    timeout=timeout
                )
            except Exception as e1:
                # 如果不支持dimensions参数，使用默认
                print(f"[QwenEmbedding] Dimensions param not supported in batch, using default: {e1}")
                response = self.client.embeddings.create(
                    model=self.model,
                    input=valid_texts,
                    timeout=timeout
                )
            
            if not response or not response.data:
                print(f"[QwenEmbedding] ERROR: Empty response from batch API")
                return [None] * len(texts)
            
            embeddings = [item.embedding for item in response.data]
            
            # 验证维度
            results = []
            for emb in embeddings:
                if not emb or len(emb) == 0:
                    print(f"[QwenEmbedding] ERROR: Got empty embedding in batch")
                    results.append(None)
                elif len(emb) != self.dimension:
                    print(f"[QwenEmbedding] ERROR: Expected {self.dimension} dims, got {len(emb)}")
                    results.append(None)  # 失败返回None
                else:
                    results.append(emb)
            
            return results
        
        except Exception as e:
            print(f"[QwenEmbedding] Batch embedding failed: {e}")
            return [None] * len(texts)  # 全部失败
