# -*- coding: utf-8 -*-
"""P2长期记忆服务（主动+被动提取，向量召回）

实现 §11.13：
- 七层流水线：安全过滤 → 触发判定 → 语义提取 → 10类分类 → 存储更新 → 检索召回 → 注入
- 主动/被动均无确认，直写（过安全门闩后）
- 硬黑名单：PII/病历/轨迹/Capability Profile/对话原文
- 10类可写：个人基础/背景/偏好/人物关系/目标计划/任务约定/知识经验/限制禁忌/健康习惯/物品设备
- 家/公司地址仅存content，无lat/lng/poi_id
"""

import re
import uuid
import hashlib
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from ..storage.database import get_db
from ..storage.models import LongTermMemoryP2


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
BLACKLIST_PATTERNS = [
    # 手机号
    (r'\b1[3-9]\d{9}\b', 'phone'),
    # 身份证号（简化版）
    (r'\b\d{17}[\dXx]\b', 'id_card'),
    # 银行卡号（简化版）
    (r'\b\d{16,19}\b', 'bank_card'),
    # 密码/token关键词
    (r'(?:密码|password|token|pwd|pass)[:：]?\s*[\w\d]{4,}', 'password'),
    # 病历关键词
    (r'(?:病历|诊断|处方|病情|症状|疾病|治疗|手术|用药|药物)', 'medical_record'),
    # GPS轨迹连续点
    (r'(?:经纬度|GPS|坐标).*?\d+\.\d+.*?\d+\.\d+', 'trajectory'),
]


class SafetyGate:
    """安全门闩：BLOCK/MASK/PASS"""
    
    @staticmethod
    def check(content: str) -> Tuple[str, Optional[str], Optional[str]]:
        """检查内容安全性
        
        Returns:
            (status, reason, cleaned_content)
            - status: 'BLOCK' | 'MASK' | 'PASS'
            - reason: 阻止原因（仅BLOCK时）
            - cleaned_content: 清洗后内容（仅PASS时）
        """
        # 检查黑名单
        for pattern, reason_type in BLACKLIST_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return ('BLOCK', f'contains_{reason_type}', None)
        
        # 检查对话原文关键词（阻止存储完整对话）
        if len(content) > 500 and ('用户说' in content or '我说' in content or '对话' in content):
            return ('BLOCK', 'dialogue_transcript', None)
        
        # 检查Capability Profile关键词
        if any(kw in content for kw in ['action', 'param_limits', 'l1_gates', 'profile_data']):
            return ('BLOCK', 'capability_profile', None)
        
        return ('PASS', None, content)


class MemoryClassifier:
    """记忆分类器：判断归属10类"""
    
    # 简单关键词分类（实际可用LLM）
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
    
    @classmethod
    def classify(cls, content: str) -> Optional[str]:
        """分类内容到10类之一
        
        Returns:
            category key or None（归不进10类则丢弃）
        """
        # 统计每个类别的关键词命中数
        scores = {cat: 0 for cat in MEMORY_CATEGORIES.keys()}
        
        for cat, keywords in cls.CATEGORY_KEYWORDS.items():
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
    """被动提取器：对话结束后异步打分 + 提取
    
    PRD v1.24 锁定公式：
    score = 0.4 * long_term + 0.3 * stability + 0.3 * personal
    threshold = 0.7（可配置）
    """
    
    # PRD v1.24: 默认阈值0.7（可通过配置调整）
    DEFAULT_THRESHOLD = 0.7
    
    @staticmethod
    def should_extract(
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
        
        # PRD v1.24: 加权公式 0.4 * long_term + 0.3 * stability + 0.3 * personal
        
        # 长期性（0.0-1.0）：不是一次性临时信息
        long_term_score = PassiveExtractor._calculate_long_term_score(utterance)
        
        # 稳定性（0.0-1.0）：不是情绪性或单次事实
        stability_score = PassiveExtractor._calculate_stability_score(utterance)
        
        # 个人属性（0.0-1.0）：关于用户自己
        personal_score = PassiveExtractor._calculate_personal_score(utterance)
        
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
    
    @staticmethod
    def extract_facts(utterance: str, response: str) -> List[str]:
        """从对话中提取事实（简化版，实际可用LLM）
        
        Returns:
            归一化的事实列表
        """
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
    """P2长期记忆服务主入口"""
    
    def __init__(self, enable_vector: bool = True):
        """
        Args:
            enable_vector: 是否启用向量召回（P0可False，P2需True）
        """
        self.enable_vector = enable_vector
        self.safety_gate = SafetyGate()
        self.classifier = MemoryClassifier()
        self.passive_extractor = PassiveExtractor()
    
    def put_memory(
        self,
        user_id: str,
        content: str,
        source_ref: str,
        trace_id: Optional[str] = None,
        is_active: bool = True
    ) -> Optional[str]:
        """写入记忆（主动/被动共用）
        
        Args:
            user_id: 用户ID（accountId:driverId格式）
            content: 记忆内容
            source_ref: 来源引用（脱敏）
            trace_id: 追踪ID
            is_active: 是否主动记忆
        
        Returns:
            memory_id or None（如被阻止）
        """
        # 1. 安全过滤
        status, reason, cleaned = self.safety_gate.check(content)
        if status == 'BLOCK':
            print(f"[P2Memory] BLOCK: {reason}")
            self._log_audit('memory_put_blocked', user_id, trace_id, reason)
            return None
        
        # 2. 分类
        category = self.classifier.classify(cleaned)
        if category is None:
            print(f"[P2Memory] Cannot classify, discarding: {cleaned[:50]}")
            return None
        
        # 3. 去重与冲突检测（简化版：同类同内容不重复写）
        db: Session = get_db()
        try:
            # 查找相似记忆
            existing = db.query(LongTermMemoryP2).filter_by(
                user_id=user_id,
                category=category
            ).all()
            
            # 简单去重：内容相似度>0.95
            for mem in existing:
                if self._similarity(mem.content, cleaned) > 0.95:
                    # 更新权重
                    mem.weight = min(mem.weight + 0.1, 2.0)
                    mem.updated_at = datetime.utcnow()
                    db.commit()
                    print(f"[P2Memory] Updated existing memory weight: {mem.memory_id}")
                    return mem.memory_id
            
            # 4. 生成embedding（如启用）
            embedding = None
            if self.enable_vector:
                embedding = self._generate_embedding(cleaned)
            
            # 5. 插入新记忆
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
            sql = text("""
                SELECT memory_id, user_id, content, category, weight, 
                       embedding <=> :query_emb AS distance
                FROM long_term_memory_p2
                WHERE user_id = :user_id AND embedding IS NOT NULL
                ORDER BY distance
                LIMIT :top_k
            """)
            
            result = db.execute(sql, {
                'query_emb': query_embedding,
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
    
    def opt_out(self, user_id: str) -> bool:
        """用户选择退出记忆功能"""
        # 实际应标记用户状态，这里简化为清空
        return self.clear_user_memories(user_id) >= 0
    
    def handle_passive_extraction(
        self,
        user_id: str,
        utterance: str,
        assistant_response: str,
        context: Dict[str, Any],
        trace_id: Optional[str] = None
    ):
        """被动提取入口（对话结束后异步调用）
        
        Args:
            user_id: 用户ID
            utterance: 用户话语
            assistant_response: 助手回复
            context: 对话上下文
            trace_id: 追踪ID
        """
        # 1. 判断是否应提取
        should_extract, confidence = self.passive_extractor.should_extract(
            utterance, assistant_response, context
        )
        
        if not should_extract:
            return
        
        print(f"[P2Memory] Passive extraction triggered (confidence={confidence:.2f})")
        
        # 2. 提取事实
        facts = self.passive_extractor.extract_facts(utterance, assistant_response)
        
        # 3. 逐条写入
        for fact in facts:
            self.put_memory(
                user_id=user_id,
                content=fact,
                source_ref=f"passive:{trace_id or 'unknown'}",
                trace_id=trace_id,
                is_active=False
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
    
    def _similarity(self, text1: str, text2: str) -> float:
        """简单相似度（实际可用embedding cosine）"""
        # 简化：字符集Jaccard
        set1 = set(text1)
        set2 = set(text2)
        if not set1 or not set2:
            return 0.0
        return len(set1 & set2) / len(set1 | set2)
    
    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """生成embedding向量（1024维）
        
        实际应调用embedding模型（如DashScope text-embedding-v3）
        这里用占位实现
        """
        try:
            # TODO: 调用真实embedding API
            # 当前占位：用hash生成伪向量
            import hashlib
            import struct
            
            hash_bytes = hashlib.sha256(text.encode('utf-8')).digest()
            # 重复hash生成1024维（每次32字节=8个float）
            vec = []
            for i in range(128):  # 128 * 8 = 1024
                h = hashlib.sha256((text + str(i)).encode('utf-8')).digest()
                for j in range(0, 32, 4):
                    val = struct.unpack('f', h[j:j+4])[0]
                    vec.append(float(val))
            
            # 归一化
            import math
            norm = math.sqrt(sum(x*x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            
            return vec[:1024]
        
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
            from ..audit.events import AuditEvent
            from ..audit.logger import AuditLogger
            
            event = AuditEvent(
                trace_id=trace_id or 'unknown',
                session_id='',
                event_type=event_type,
                timestamp=datetime.utcnow().isoformat() + 'Z',
                status='ok' if 'blocked' not in event_type else 'blocked',
                reason=reason,
                metadata={'user_id': user_id}
            )
            
            logger = AuditLogger()
            logger.log_event(event)
        except Exception as e:
            print(f"[P2Memory] Audit log error: {e}")
