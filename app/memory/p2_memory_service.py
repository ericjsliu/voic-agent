# -*- coding: utf-8 -*-
"""P2长期记忆服务（主动+被动提取，向量召回）

实现 §11.13：
- 七层流水线：安全过滤 → 触发判定 → 语义提取 → 10类分类 → 存储更新 → 检索召回 → 注入
- 主动/被动均无确认，直写（过安全门闩后）
- 硬黑名单：PII/病历/轨迹/Capability Profile/对话原文
- 10类可写：个人基础/背景/偏好/人物关系/目标计划/任务约定/知识经验/限制禁忌/健康习惯/物品设备
- 家/公司地址仅存content，无lat/lng/poi_id

模型路由（PRD v1.24 + Model Lock）：
- memory_extract: MEMORY_EXTRACT_MODEL (default: qwen-turbo) - 被动评分 + 事实提取 + 10类分类
- memory_embed: MEMORY_EMBED_MODEL (default: text-embedding-v3, 1024维) - 向量embedding
- 全部使用 DashScope/Qwen，通过 OpenAI-compatible API
"""

import re
import os
import uuid
import hashlib
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
import openai

from ..storage.database import get_db
from ..storage.models import LongTermMemoryP2
from .passive_queue import MemoryControlStore, PassiveQueueWorker


# 10类可写分类
MEMORY_CATEGORIES = {
    "personal_basic": "个人基础信息",      # 家/公司地址、称呼等
    "personal_background": "个人背景",    # 职业、教育等
    "user_preference": "用户偏好",        # 空调温度、音乐偏好等
    "relationships": "人物关系",          # 家人、朋友等
    "goals_plans": "目标与计划",          # 未来计划等
    "tasks_agreements": "任务与约定",     # 待办事项等
    "knowledge_experience": "知识与经验", # 学到的东西等
    "restrictions": "限制与禁忌",         # 不喜欢的话题等
    "health_habits": "个人健康习惯",      # 非病历级：运动习惯等
    "items_devices": "物品与设备"         # 拥有的设备等
}


# 硬黑名单模式（PII等）
# 注意：不使用 \b 因为中文前面的 \b 不生效
# MASKABLE：可剥离后保留非敏感事实；HARD_BLOCK：整段丢弃
# 顺序：更长/更具体的模式优先（避免身份证内部被手机号模式误切）
BLACKLIST_PATTERNS = [
    # 身份证号（15位或18位，18位最后可能是X）— 先于手机号匹配
    (r'(?<!\d)\d{17}[\dXx](?!\d)', 'id_card', 'MASKABLE'),
    (r'(?<!\d)\d{15}(?!\d)', 'id_card', 'MASKABLE'),
    # 银行卡号（16-19位数字）
    (r'(?<!\d)\d{16,19}(?!\d)', 'bank_card', 'MASKABLE'),
    # 手机号（中国大陆：支持 +86/86、空格、横线；两侧不得紧贴更多数字）
    (r'(?<!\d)(?:\+?86[-\s]?)?1[3-9](?:[-\s]?\d){9}(?!\d)', 'phone', 'MASKABLE'),
    # 密码/token关键词 → 硬拦
    (r'(?:密码|password|token|pwd|pass)[:：]?\s*[\w\d]{4,}', 'password', 'HARD_BLOCK'),
    # 病历关键词 → 硬拦
    (r'(?:病历|诊断|处方|病情|症状|疾病|治疗|手术|用药|药物)', 'medical_record', 'HARD_BLOCK'),
    # GPS轨迹连续点 → 硬拦
    (r'(?:经纬度|GPS|坐标).*?\d+\.\d+.*?\d+\.\d+', 'trajectory', 'HARD_BLOCK'),
]


class SafetyGate:
    """安全门闩：BLOCK/MASK/PASS"""
    
    # 脱敏后若只剩这些载体词，视为无可写非敏感事实 → BLOCK
    _PII_CARRIER_PATTERNS = [
        r'手机号?',
        r'电话',
        r'号码',
        r'身份证(?:号)?',
        r'银行卡(?:号)?',
        r'联系方式',
        r'我的号',
        r'这个号码',
        r'(?:记住|记下|保存)',
        r'[是为在的]',
    ]
    
    @staticmethod
    def _strip_maskable(content: str) -> Tuple[str, List[str]]:
        """剥离可脱敏敏感片段，返回 (清洗后文本, 命中类型列表)"""
        cleaned = content
        hit_types: List[str] = []
        for pattern, reason_type, severity in BLACKLIST_PATTERNS:
            if severity != 'MASKABLE':
                continue
            if re.search(pattern, cleaned, re.IGNORECASE):
                hit_types.append(reason_type)
                cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        # 清理脱敏后残留标点/空白
        cleaned = re.sub(r'[，,、；;：:\s]{2,}', '，', cleaned)
        cleaned = re.sub(r'^[\s，,、；;：:]+|[\s，,、；;：:]+$', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned, hit_types
    
    @classmethod
    def _strip_carriers(cls, text: str) -> str:
        """去掉脱敏后残留的 PII 载体词（如「手机号是」）。"""
        residual = text
        for pattern in cls._PII_CARRIER_PATTERNS:
            residual = re.sub(pattern, '', residual, flags=re.IGNORECASE)
        residual = re.sub(r'[，,、；;：:\s]{2,}', '，', residual)
        residual = re.sub(r'^[\s，,、；;：:]+|[\s，,、；;：:]+$', '', residual)
        return residual.strip()
    
    @classmethod
    def _has_non_sensitive_fact(cls, cleaned: str) -> bool:
        """判断脱敏后是否仍有可写的非敏感事实（排除「手机号是」等空壳）"""
        residual = cls._strip_carriers(cleaned)
        residual = re.sub(r'[\s，,、；;：:\.\-_/\\]+', '', residual)
        if len(residual) < 4:
            return False
        # 纯指代残片
        if residual in {'我', '住我', '记住', '这个', '号码', '电话是', '住我的'}:
            return False
        return True
    
    @staticmethod
    def check(content: str) -> Tuple[str, Optional[str], Optional[str]]:
        """检查内容安全性
        
        Returns:
            (status, reason, cleaned_content)
            - status: 'BLOCK' | 'MASK' | 'PASS'
            - reason: 阻止/脱敏原因
            - cleaned_content: 可写内容（PASS/MASK 时有值；BLOCK 为 None）
        """
        # 1. 硬拦：密码/病历/轨迹等不可脱敏
        for pattern, reason_type, severity in BLACKLIST_PATTERNS:
            if severity != 'HARD_BLOCK':
                continue
            if re.search(pattern, content, re.IGNORECASE):
                return ('BLOCK', f'contains_{reason_type}', None)
        
        # 检查对话原文关键词（阻止存储完整对话）
        if len(content) > 500 and ('用户说' in content or '我说' in content or '对话' in content):
            return ('BLOCK', 'dialogue_transcript', None)
        
        # 检查Capability Profile关键词
        if any(kw in content for kw in ['action', 'param_limits', 'l1_gates', 'profile_data']):
            return ('BLOCK', 'capability_profile', None)
        
        # 2. 可脱敏：剥离手机号/证件/银行卡后保留非敏感事实
        cleaned, hit_types = SafetyGate._strip_maskable(content)
        if hit_types:
            # 手机号/身份证本身永不入库；仅当脱敏后仍有独立非敏感事实才 MASK
            if not SafetyGate._has_non_sensitive_fact(cleaned):
                return ('BLOCK', f'contains_{hit_types[0]}', None)
            # 写入前再剥载体词，避免「喜欢听周杰伦，手机号是」入库
            cleaned = SafetyGate._strip_carriers(cleaned)
            reason = 'masked_' + '+'.join(sorted(set(hit_types)))
            return ('MASK', reason, cleaned)
        
        return ('PASS', None, content)


class MemoryClassifier:
    """记忆分类器：判断归属10类
    
    使用 Qwen LLM 进行智能分类（优先），关键词匹配作为fallback
    """
    
    # 简单关键词分类（fallback）
    CATEGORY_KEYWORDS = {
        "personal_basic": ["家", "公司", "地址", "住址", "称呼", "叫我", "名字"],
        "user_preference": ["喜欢", "偏好", "习惯", "常听", "常看", "温度", "空调"],
        "relationships": ["老婆", "丈夫", "父母", "孩子", "朋友", "同事"],
        "goals_plans": ["打算", "计划", "目标", "想要", "准备"],
        "tasks_agreements": ["提醒", "记得", "待办", "约定"],
        "restrictions": ["不要", "别", "禁止", "不喜欢", "不要提"],
        "health_habits": ["运动", "跑步", "健身", "睡眠", "饮食习惯"],
        "items_devices": ["车", "手机", "设备", "买了", "拥有"],
        "knowledge_experience": ["学会", "了解", "知道了", "记住"],
        "personal_background": ["职业", "工作", "毕业", "学校"],
    }
    
    def __init__(self, llm_client: Optional['QwenMemoryExtractor'] = None):
        """
        Args:
            llm_client: Qwen记忆提取客户端（如为None则使用关键词fallback）
        """
        self.llm_client = llm_client
    
    def classify(self, content: str, llm_result: Optional[Dict] = None) -> Optional[str]:
        """分类内容到10类之一
        
        Args:
            content: 内容文本
            llm_result: LLM提取结果（如有）
        
        Returns:
            category key or None（归不进10类则丢弃）
        """
        # 如果LLM已经分类，直接使用
        if llm_result and 'category' in llm_result:
            category = llm_result['category']
            if category in MEMORY_CATEGORIES:
                return category
            print(f"[MemoryClassifier] LLM category not in 10 classes: {category}")
        
        # Fallback: 关键词分类
        # 统计每个类别的关键词命中数
        scores = {cat: 0 for cat in MEMORY_CATEGORIES.keys()}
        
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in content:
                    scores[cat] += 1
        
        # 取得分最高的类别
        max_score = max(scores.values())
        if max_score == 0:
            return None  # 无法分类，丢弃
        
        for cat, score in scores.items():
            if score == max_score:
                return cat
        
        return None


class PassiveExtractor:
    """被动提取器：打分 + 提取（PRD v1.27修正: 仅由scheduled batch job调用）
    
    PRD v1.24 锁定公式：
    score = 0.4 * long_term + 0.3 * stability + 0.3 * personal
    threshold = 0.7（可配置）
    
    PRD v1.27修正 触发时机：
    - 仅由scheduled batch job触发（nightly/每N小时）
    - Batch job扫描PG task/audit records
    - 不在Task terminal state触发
    - 不在Session idle触发
    
    使用 Qwen LLM 进行智能评分和事实提取
    """
    
    # PRD v1.24: 默认阈值0.7（可通过配置调整）
    DEFAULT_THRESHOLD = 0.7
    
    def __init__(self, llm_client: Optional['QwenMemoryExtractor'] = None):
        """
        Args:
            llm_client: Qwen记忆提取客户端（如为None则使用规则fallback）
        """
        self.llm_client = llm_client
    
    def should_extract(
        self,
        utterance: str,
        assistant_response: str,
        context: Dict[str, Any],
        threshold: float = None
    ) -> Tuple[bool, float]:
        """判断是否应该被动提取（PRD v1.24锁定公式）
        
        Args:
            utterance: 用户话语
            assistant_response: 助手回复
            context: 对话上下文
            threshold: 阈值（默认0.7）
        
        Returns:
            (should_extract, score)
        """
        if threshold is None:
            threshold = PassiveExtractor.DEFAULT_THRESHOLD
        
        # 如果有LLM客户端，使用LLM评分
        if self.llm_client:
            try:
                result = self.llm_client.score_utterance(utterance, assistant_response, timeout=3.0)
                if result:
                    score = result.get('score', 0.0)
                    return (score >= threshold, score)
            except Exception as e:
                print(f"[PassiveExtractor] LLM scoring failed, fallback to rules: {e}")
        
        # Fallback: 规则评分（PRD v1.24加权公式）
        long_term_score = self._calculate_long_term_score(utterance)
        stability_score = self._calculate_stability_score(utterance)
        personal_score = self._calculate_personal_score(utterance)
        
        # 加权求和
        score = 0.4 * long_term_score + 0.3 * stability_score + 0.3 * personal_score
        
        return (score >= threshold, score)
    
    @staticmethod
    def _calculate_long_term_score(utterance: str) -> float:
        """计算长期性得分（PRD v1.24）
        
        低长期性（应跳过）：
        - 一次性交通查询（堵车吗、路况）
        - 下个路口类问题
        - 临时车辆状态查询
        
        Returns:
            0.0-1.0
        """
        # 明确的低长期性模式（PRD v1.24锁定）
        low_long_term_patterns = [
            '堵车', '路况', '拥堵',  # 一次性交通查询
            '下个路口', '下一个路口', '前方路口',  # next intersection
            '当前', '现在', '目前',  # ephemeral state
            '几点', '多少', '什么时候',  # temporal queries
        ]
        
        for pattern in low_long_term_patterns:
            if pattern in utterance:
                return 0.1  # 低长期性，几乎不提取
        
        # 高长期性模式
        high_long_term_patterns = [
            '我', '我的', '家', '公司', '住址',
            '喜欢', '习惯', '偏好', '常', '经常',
            '总是', '一直', '平时', '通常'
        ]
        
        for pattern in high_long_term_patterns:
            if pattern in utterance:
                return 0.9  # 高长期性
        
        # 默认中等
        return 0.5
    
    @staticmethod
    def _calculate_stability_score(utterance: str) -> float:
        """计算稳定性得分
        
        Returns:
            0.0-1.0
        """
        # 高稳定性（持久模式）
        if any(kw in utterance for kw in ['总是', '一直', '经常', '平时', '通常', '习惯']):
            return 0.9
        
        # 低稳定性（临时/情绪）
        if any(kw in utterance for kw in ['今天', '现在', '刚才', '这次', '暂时']):
            return 0.2
        
        # 默认中等
        return 0.5
    
    @staticmethod
    def _calculate_personal_score(utterance: str) -> float:
        """计算个人属性得分
        
        Returns:
            0.0-1.0
        """
        # 明确的个人属性
        if any(kw in utterance for kw in ['我', '我的', '我家', '我常']):
            return 0.9
        
        # 非个人（通用查询）
        if any(kw in utterance for kw in ['这个', '那个', '怎么', '什么', '如何']):
            return 0.3
        
        # 默认中等
        return 0.5
    
    def extract_with_meta(self, utterance: str, response: str) -> Dict[str, Any]:
        """提取事实并带回 LLM category（供 put_memory 分类打通）
        
        Returns:
            {
                'facts': List[str],
                'category': Optional[str],   # 10类之一或None
                'llm_result': Optional[Dict] # 原始LLM结果，可直接传 classify
            }
        """
        # 优先 LLM：同时拿 facts + category
        if self.llm_client:
            try:
                result = self.llm_client.extract_facts(utterance, response, timeout=3.0)
                if result and result.get('facts'):
                    category = result.get('category')
                    if category and category not in MEMORY_CATEGORIES:
                        print(f"[PassiveExtractor] LLM category out of 10: {category}")
                        category = None
                    return {
                        'facts': list(result['facts']),
                        'category': category,
                        'llm_result': result,
                    }
            except Exception as e:
                print(f"[PassiveExtractor] LLM extraction failed, fallback to rules: {e}")
        
        # Fallback: 规则提取（无 LLM category）
        return {
            'facts': self._extract_facts_by_rules(utterance),
            'category': None,
            'llm_result': None,
        }
    
    def extract_facts(self, utterance: str, response: str) -> List[str]:
        """从对话中提取事实（兼容旧接口，仅返回事实列表）"""
        return self.extract_with_meta(utterance, response)['facts']
    
    @staticmethod
    def _extract_facts_by_rules(utterance: str) -> List[str]:
        """规则提取事实（LLM 不可用时的 fallback）"""
        facts = []
        
        # 简单提取模式
        # "我平时喜欢听周杰伦" -> "喜欢听周杰伦"
        patterns = [
            (r'我(?:平时|经常|总是)?(?:喜欢|爱听|爱看)(.+)', r'喜欢\1'),
            (r'我家(?:在|住在)(.+)', r'家地址：\1'),
            (r'公司(?:在|地址是)(.+)', r'公司地址：\1'),
            (r'(?:叫我|称呼我)(.+)', r'称呼：\1'),
            (r'(?:空调|温度).*?(\d+)度', r'偏好空调温度：\1度'),
        ]
        
        for pattern, replacement in patterns:
            match = re.search(pattern, utterance)
            if match:
                fact = re.sub(pattern, replacement, match.group(0))
                facts.append(fact)
        
        return facts


class P2MemoryService:
    """P2长期记忆服务主入口
    
    集成 Qwen 模型：
    - memory_extract (qwen-turbo): 被动评分 + 事实提取 + 10类分类
    - memory_embed (text-embedding-v3): 向量embedding (1536维)
    """
    
    def __init__(
        self, 
        enable_vector: bool = True,
        extract_model: Optional[str] = None,
        embed_model: Optional[str] = None,
        control_store: Optional[MemoryControlStore] = None,
        start_worker: bool = False,
    ):
        """
        Args:
            enable_vector: 是否启用向量召回（P0可False，P2需True）
            extract_model: 提取模型名称（默认qwen-turbo）
            embed_model: Embedding模型名称（默认text-embedding-v3）
            control_store: opt_out + 被动队列存储（可注入便于测试）
            start_worker: 是否立即启动被动队列消费者
        """
        self.enable_vector = enable_vector
        self.safety_gate = SafetyGate()
        self.control_store = control_store or MemoryControlStore()
        self._passive_worker: Optional[PassiveQueueWorker] = None
        
        # 初始化Qwen客户端（失败时使用规则fallback）
        try:
            from .qwen_clients import QwenMemoryExtractor, QwenEmbedding
            
            self.extractor_client = QwenMemoryExtractor(model=extract_model)
            self.classifier = MemoryClassifier(llm_client=self.extractor_client)
            self.passive_extractor = PassiveExtractor(llm_client=self.extractor_client)
            
            if enable_vector:
                self.embedding_client = QwenEmbedding(model=embed_model)
            else:
                self.embedding_client = None
            
            print(f"[P2Memory] Initialized with Qwen models (extract={self.extractor_client.model}, embed={self.embedding_client.model if self.embedding_client else 'disabled'})")
        
        except Exception as e:
            print(f"[P2Memory] Warning: Qwen client init failed, using rule fallback: {e}")
            self.extractor_client = None
            self.classifier = MemoryClassifier(llm_client=None)
            self.passive_extractor = PassiveExtractor(llm_client=None)
            self.embedding_client = None
        
        if start_worker:
            self.start_passive_worker()
    
    def put_memory(
        self,
        user_id: str,
        content: str,
        source_ref: str,
        trace_id: Optional[str] = None,
        is_active: bool = True,
        category: Optional[str] = None,
        llm_extract_result: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """写入记忆（主动/被动共用）
        
        Args:
            user_id: 用户ID（accountId:driverId格式）
            content: 记忆内容
            source_ref: 来源引用（脱敏）
            trace_id: 追踪ID
            is_active: 是否主动记忆
            category: 可选，调用方已确定的10类（被动LLM路径优先传入）
            llm_extract_result: 可选，LLM提取结果（含 category/facts），供分类器优先使用
        
        Returns:
            memory_id or None（如被阻止）
        """
        # 0. 关记忆：不写
        if self.is_opted_out(user_id):
            print(f"[P2Memory] opt_out skip put: {user_id}")
            self._log_audit('memory_put_blocked', user_id, trace_id, 'opt_out')
            return None
        
        # 1. 安全过滤（BLOCK 丢弃；MASK 用脱敏后文本；PASS 原文）
        status, reason, cleaned = self.safety_gate.check(content)
        if status == 'BLOCK':
            print(f"[P2Memory] BLOCK: {reason}")
            self._log_audit('memory_put_blocked', user_id, trace_id, reason)
            return None
        if status == 'MASK':
            print(f"[P2Memory] MASK: {reason} -> {cleaned[:50] if cleaned else ''}")
            self._log_audit('memory_put_masked', user_id, trace_id, reason)
            if not cleaned:
                return None
        # PASS / MASK 均使用 cleaned 继续下游
        
        # 2. 分类：显式 category > LLM 结果 > 关键词 fallback（禁止写死 llm_extract_result=None）
        if category and category in MEMORY_CATEGORIES:
            resolved_category = category
        else:
            resolved_category = self.classifier.classify(
                cleaned, llm_result=llm_extract_result
            )
        category = resolved_category
        if category is None:
            print(f"[P2Memory] Cannot classify, discarding: {cleaned[:50]}")
            return None
        
        # 3. 去重与冲突检测：向量相似≥0.95 合并；同维冲突升 version_id 覆盖
        db: Session = get_db()
        try:
            # 先生成 embedding（用于向量去重；失败则后续走字符 fallback）
            embedding = None
            if self.enable_vector:
                embedding = self._generate_embedding(cleaned)
            
            existing = db.query(LongTermMemoryP2).filter_by(
                user_id=user_id,
                category=category
            ).all()
            
            for mem in existing:
                # 3a. 同维冲突：家/公司/称呼等同槽位新盖旧，升 version_id
                if self._is_same_dimension_conflict(mem.content, cleaned):
                    mem.content = cleaned
                    if embedding is not None:
                        mem.embedding = embedding
                    mem.version_id = int(mem.version_id or 1) + 1
                    mem.weight = max(float(mem.weight or 1.0), 1.0)
                    mem.source_ref = source_ref
                    mem.updated_at = datetime.utcnow()
                    db.commit()
                    print(
                        f"[P2Memory] Conflict overwrite version={mem.version_id}: {mem.memory_id}"
                    )
                    self._log_audit('memory_put', user_id, trace_id, f'conflict_v{mem.version_id}')
                    return mem.memory_id
                
                # 3b. 向量去重：cosine ≥ 0.95 合并（不双插）
                mem_emb = self._coerce_embedding(mem.embedding)
                if embedding is not None and mem_emb is not None:
                    if self._cosine_similarity(embedding, mem_emb) >= 0.95:
                        mem.weight = min(float(mem.weight or 1.0) + 0.1, 2.0)
                        mem.updated_at = datetime.utcnow()
                        db.commit()
                        print(f"[P2Memory] Vector dedup merge: {mem.memory_id}")
                        self._log_audit('memory_put', user_id, trace_id, 'vector_dedup')
                        return mem.memory_id
                else:
                    # 无向量时字符 Jaccard fallback（仅降级）
                    if self._char_jaccard(mem.content, cleaned) > 0.95:
                        mem.weight = min(float(mem.weight or 1.0) + 0.1, 2.0)
                        mem.updated_at = datetime.utcnow()
                        db.commit()
                        print(f"[P2Memory] Jaccard dedup merge: {mem.memory_id}")
                        return mem.memory_id
            
            # 4. 插入新记忆
            memory_id = str(uuid.uuid4())
            memory = LongTermMemoryP2(
                memory_id=memory_id,
                user_id=user_id,
                content=cleaned,
                category=category,
                embedding=embedding,
                weight=1.0,
                version_id=1,
                source_ref=source_ref
            )
            db.add(memory)
            db.commit()
            
            print(f"[P2Memory] Saved: {memory_id} | {category} | {cleaned[:50]}")
            self._log_audit('memory_put', user_id, trace_id, category)
            
            return memory_id
        
        except Exception as e:
            db.rollback()
            print(f"[P2Memory] Error saving memory: {e}")
            return None
        finally:
            db.close()
    
    def search_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        token_budget: int = 300,
        trace_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """向量召回记忆（P2）
        
        Args:
            user_id: 用户ID
            query: 查询文本
            top_k: 召回数量（≤5）
            token_budget: Token预算（≤300）
            trace_id: 追踪ID
        
        Returns:
            记忆列表（按weight×similarity重排）
        """
        # 关记忆：不召回
        if self.is_opted_out(user_id):
            print(f"[P2Memory] opt_out skip search: {user_id}")
            return []
        
        if not self.enable_vector:
            # P0: 仅返回KV热点（家/公司/偏好）
            return self._get_hot_memories(user_id)
        
        db: Session = get_db()
        try:
            # 生成query embedding
            query_embedding = self._generate_embedding(query)
            if query_embedding is None:
                return []
            
            # 向量相似度搜索（pgvector）
            from sqlalchemy import text
            
            # 粗召回：TopK
            # pgvector needs explicit cast; binding a Python list becomes numeric[]
            emb_literal = "[" + ",".join(str(float(x)) for x in query_embedding) + "]"
            sql = text("""
                SELECT memory_id, user_id, content, category, weight,
                       embedding <=> CAST(:query_emb AS vector) AS distance
                FROM long_term_memory_p2
                WHERE user_id = :user_id AND embedding IS NOT NULL
                ORDER BY distance
                LIMIT :top_k
            """)
            
            result = db.execute(sql, {
                'query_emb': emb_literal,
                'user_id': user_id,
                'top_k': min(top_k, 5)
            })
            
            memories = []
            total_tokens = 0
            
            for row in result:
                # 重排序：weight × (1 - distance)
                similarity = 1.0 - row.distance
                score = row.weight * similarity
                
                # 估算token数（简化：中文约2字符/token）
                estimated_tokens = len(row.content) // 2
                if total_tokens + estimated_tokens > token_budget:
                    break
                
                memories.append({
                    'memory_id': row.memory_id,
                    'content': row.content,
                    'category': row.category,
                    'weight': row.weight,
                    'similarity': similarity,
                    'score': score
                })
                total_tokens += estimated_tokens
            
            # 按score降序
            memories.sort(key=lambda x: x['score'], reverse=True)
            
            self._log_audit('memory_search', user_id, trace_id, f'found_{len(memories)}')
            return memories[:min(len(memories), 5)]
        
        except Exception as e:
            print(f"[P2Memory] Error searching memories: {e}")
            return []
        finally:
            db.close()
    
    def list_memories(
        self,
        user_id: str,
        category: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """列出用户记忆"""
        db: Session = get_db()
        try:
            query = db.query(LongTermMemoryP2).filter_by(user_id=user_id)
            if category:
                query = query.filter_by(category=category)
            
            records = query.order_by(
                LongTermMemoryP2.updated_at.desc()
            ).limit(limit).all()
            
            return [
                {
                    'memory_id': r.memory_id,
                    'content': r.content,
                    'category': r.category,
                    'weight': r.weight,
                    'created_at': r.created_at.isoformat(),
                    'updated_at': r.updated_at.isoformat()
                }
                for r in records
            ]
        finally:
            db.close()
    
    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        """删除单条记忆"""
        db: Session = get_db()
        try:
            record = db.query(LongTermMemoryP2).filter_by(
                memory_id=memory_id,
                user_id=user_id
            ).first()
            
            if record:
                db.delete(record)
                db.commit()
                print(f"[P2Memory] Deleted: {memory_id}")
                return True
            return False
        except Exception as e:
            db.rollback()
            print(f"[P2Memory] Error deleting memory: {e}")
            return False
        finally:
            db.close()
    
    def clear_user_memories(self, user_id: str) -> int:
        """清空用户所有记忆（换驾驶员时调用）"""
        db: Session = get_db()
        try:
            count = db.query(LongTermMemoryP2).filter_by(user_id=user_id).delete()
            db.commit()
            print(f"[P2Memory] Cleared {count} memories for user: {user_id}")
            return count
        except Exception as e:
            db.rollback()
            print(f"[P2Memory] Error clearing memories: {e}")
            return 0
        finally:
            db.close()
    
    def is_opted_out(self, user_id: str) -> bool:
        """账号是否已关闭记忆功能。"""
        store = getattr(self, "control_store", None)
        if store is None:
            return False
        try:
            return store.is_opted_out(user_id)
        except Exception as e:
            print(f"[P2Memory] opt_out check failed: {e}")
            return False
    
    def opt_out(self, user_id: str) -> bool:
        """用户选择退出记忆功能：持久化开关 + 尽力清空已有记忆。"""
        try:
            self.control_store.set_opt_out(user_id, True)
        except Exception as e:
            print(f"[P2Memory] opt_out flag failed: {e}")
            return False
        # 清空失败不回滚开关：关记忆优先于清库
        try:
            self.clear_user_memories(user_id)
        except Exception as e:
            print(f"[P2Memory] opt_out clear memories soft-fail: {e}")
        print(f"[P2Memory] opt_out enabled for {user_id}")
        return True
    
    def opt_in(self, user_id: str) -> bool:
        """重新开启记忆功能（仅清开关，不恢复历史）。"""
        try:
            self.control_store.set_opt_out(user_id, False)
            print(f"[P2Memory] opt_in enabled for {user_id}")
            return True
        except Exception as e:
            print(f"[P2Memory] opt_in failed: {e}")
            return False
    
    def enqueue_passive_extraction(
        self,
        user_id: str,
        utterance: str,
        assistant_response: str,
        context: Dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> Optional[str]:
        """将被动提取任务写入持久化队列（进程重启不丢）。"""
        if self.is_opted_out(user_id):
            print(f"[P2Memory] opt_out skip enqueue passive: {user_id}")
            return None
        job_id = self.control_store.enqueue({
            "user_id": user_id,
            "utterance": utterance,
            "assistant_response": assistant_response or "",
            "context": context or {},
            "trace_id": trace_id,
        })
        print(f"[P2Memory] Enqueued passive job {job_id}")
        return job_id
    
    def start_passive_worker(self) -> None:
        """启动被动队列消费者。"""
        if self._passive_worker is not None:
            return
        
        def _handle(payload: Dict[str, Any]) -> None:
            self.handle_passive_extraction(
                user_id=payload.get("user_id", ""),
                utterance=payload.get("utterance", ""),
                assistant_response=payload.get("assistant_response", ""),
                context=payload.get("context") or {},
                trace_id=payload.get("trace_id"),
            )
        
        self._passive_worker = PassiveQueueWorker(self.control_store, _handle)
        self._passive_worker.start()
    
    def stop_passive_worker(self) -> None:
        """停止被动队列消费者。"""
        if self._passive_worker is not None:
            self._passive_worker.stop()
            self._passive_worker = None
    
    def handle_passive_extraction(
        self,
        user_id: str,
        utterance: str,
        assistant_response: str,
        context: Dict[str, Any],
        trace_id: Optional[str] = None
    ):
        """被动提取入口（PRD v1.27修正: 仅由scheduled batch job通过PassiveMemoryConsumer调用）
        
        触发时机：
        - 仅scheduled batch job（nightly/每N小时）
        - Batch job扫描PG task/audit records或Redis队列
        - 不在对话回合中调用
        - 不在Task terminal state调用
        
        Args:
            user_id: 用户ID
            utterance: 用户话语
            assistant_response: 助手回复
            context: 对话上下文
            trace_id: 追踪ID
        """
        # 0. 关记忆：不写
        if self.is_opted_out(user_id):
            print(f"[P2Memory] opt_out skip passive: {user_id}")
            return
        
        # 1. 判断是否应提取
        should_extract, confidence = self.passive_extractor.should_extract(
            utterance, assistant_response, context
        )
        
        if not should_extract:
            return
        
        print(f"[P2Memory] Passive extraction triggered (confidence={confidence:.2f})")
        
        # 2. 提取事实 + category（LLM 结果打通 put，不再丢弃分类）
        meta = self.passive_extractor.extract_with_meta(utterance, assistant_response)
        facts = meta.get('facts') or []
        category = meta.get('category')
        llm_result = meta.get('llm_result')
        
        if not facts:
            return
        
        # 3. 逐条写入（带上 LLM category）
        for fact in facts:
            self.put_memory(
                user_id=user_id,
                content=fact,
                source_ref=f"passive:{trace_id or 'unknown'}",
                trace_id=trace_id,
                is_active=False,
                category=category,
                llm_extract_result=llm_result,
            )
    
    def parse_home_company_address(self, user_id: str) -> Dict[str, Optional[str]]:
        """解析家/公司地址（供导航使用）
        
        Returns:
            {'home': 'xxx路xxx号', 'company': 'xxx路xxx号'}
        """
        db: Session = get_db()
        try:
            records = db.query(LongTermMemoryP2).filter_by(
                user_id=user_id,
                category='personal_basic'
            ).all()
            
            home_addr = None
            company_addr = None
            
            for record in records:
                content = record.content
                if content.startswith('家地址：'):
                    home_addr = content.replace('家地址：', '').strip()
                elif content.startswith('公司地址：'):
                    company_addr = content.replace('公司地址：', '').strip()
            
            return {'home': home_addr, 'company': company_addr}
        finally:
            db.close()
    
    # ========== 内部辅助方法 ==========
    
    # 同维槽位前缀：同前缀不同值 → 冲突覆盖
    _DIMENSION_PREFIXES = (
        '家地址：',
        '公司地址：',
        '称呼：',
        '偏好空调温度：',
    )
    
    @classmethod
    def _is_same_dimension_conflict(cls, old_content: str, new_content: str) -> bool:
        """同维冲突：同一槽位（家/公司/称呼等）新旧值不同。"""
        if not old_content or not new_content:
            return False
        if old_content == new_content:
            return False
        for prefix in cls._DIMENSION_PREFIXES:
            if old_content.startswith(prefix) and new_content.startswith(prefix):
                return True
        return False
    
    @staticmethod
    def _coerce_embedding(raw: Any) -> Optional[List[float]]:
        """把 DB/pgvector 返回值转为 float list。"""
        if raw is None:
            return None
        if isinstance(raw, list):
            try:
                return [float(x) for x in raw]
            except (TypeError, ValueError):
                return None
        if isinstance(raw, str):
            try:
                # pgvector 有时以 "[1,2,...]" 文本返回
                text = raw.strip()
                if text.startswith('[') and text.endswith(']'):
                    parts = text[1:-1].split(',')
                    return [float(p) for p in parts if p.strip()]
            except (TypeError, ValueError):
                return None
        try:
            return [float(x) for x in list(raw)]
        except (TypeError, ValueError):
            return None
    
    @staticmethod
    def _cosine_similarity(emb1: List[float], emb2: List[float]) -> float:
        """余弦相似度（向量去重用）。"""
        if not emb1 or not emb2 or len(emb1) != len(emb2):
            return 0.0
        dot = 0.0
        n1 = 0.0
        n2 = 0.0
        for a, b in zip(emb1, emb2):
            dot += a * b
            n1 += a * a
            n2 += b * b
        if n1 <= 0.0 or n2 <= 0.0:
            return 0.0
        return dot / ((n1 ** 0.5) * (n2 ** 0.5))
    
    def _similarity(self, text1: str, text2: str) -> float:
        """兼容旧接口：字符 Jaccard（仅无向量时的降级）。"""
        return self._char_jaccard(text1, text2)
    
    @staticmethod
    def _char_jaccard(text1: str, text2: str) -> float:
        """字符集 Jaccard（无 embedding 时的降级去重）。"""
        set1 = set(text1 or '')
        set2 = set(text2 or '')
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)
    
    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """生成embedding向量（1024维，用户锁定text-embedding-v3）
        
        使用 Qwen Embedding API
        """
        if not self.embedding_client:
            print(f"[P2Memory] Embedding client not initialized, skipping embedding")
            return None
        
        try:
            embedding = self.embedding_client.embed(text, timeout=5.0)
            if embedding and len(embedding) > 0:
                expected_dim = int(os.getenv('EMBEDDING_DIMENSIONS', '1024'))
                if len(embedding) != expected_dim:
                    print(f"[P2Memory] ERROR: Invalid embedding dimension: {len(embedding)}, expected {expected_dim}")
                    return None  # 绝不写入错误维度的向量
                return embedding
            else:
                print(f"[P2Memory] Invalid embedding dimension: {len(embedding) if embedding else 0}, skipping")
                return None
        
        except Exception as e:
            print(f"[P2Memory] Error generating embedding: {e}")
            return None
    
    def _get_hot_memories(self, user_id: str) -> List[Dict[str, Any]]:
        """P0热点记忆（无向量时的fallback）"""
        db: Session = get_db()
        try:
            # 仅返回家/公司/偏好
            records = db.query(LongTermMemoryP2).filter_by(
                user_id=user_id
            ).filter(
                LongTermMemoryP2.category.in_([
                    'personal_basic', 'user_preference'
                ])
            ).order_by(
                LongTermMemoryP2.weight.desc()
            ).limit(5).all()
            
            return [
                {
                    'memory_id': r.memory_id,
                    'content': r.content,
                    'category': r.category,
                    'weight': r.weight
                }
                for r in records
            ]
        finally:
            db.close()
    
    def _log_audit(
        self,
        event_type: str,
        user_id: str,
        trace_id: Optional[str],
        reason: Optional[str]
    ):
        """记录审计事件（不含原文）"""
        try:
            from ..audit.events import AuditEvent, AuditEventType
            from ..audit.logger import AuditLogger
            
            # 字符串事件名映射到枚举（兼容历史调用）
            type_map = {
                'memory_put': AuditEventType.MEMORY_PUT,
                'memory_search': AuditEventType.MEMORY_SEARCH,
                'memory_put_blocked': AuditEventType.MEMORY_PUT_BLOCKED,
                'memory_put_masked': AuditEventType.MEMORY_PUT_MASKED,
            }
            resolved_type = type_map.get(event_type)
            if resolved_type is None:
                try:
                    resolved_type = AuditEventType(event_type)
                except ValueError:
                    print(f"[P2Memory] Unknown audit event type: {event_type}")
                    return
            
            event = AuditEvent(
                trace_id=trace_id or 'unknown',
                session_id='',
                event_type=resolved_type,
                timestamp=datetime.utcnow().isoformat() + 'Z',
                status='ok' if 'blocked' not in event_type else 'blocked',
                reason=reason,
                metadata={'user_id': user_id}
            )
            
            logger = AuditLogger()
            logger.log_event(event)
        except Exception as e:
            print(f"[P2Memory] Audit log error: {e}")
