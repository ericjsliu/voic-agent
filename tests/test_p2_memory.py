# -*- coding: utf-8 -*-
"""P2长期记忆系统测试

测试覆盖：
- M-H1: 记住家地址（content only, no coords）
- M-P1: 被动偏好提取
- M-B1: PII黑名单阻止
- M-N1: 导航回家（从content解析地址，resolve POI）
- M-D1: 驾驶员隔离
"""

import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.memory.p2_memory_service import (
    P2MemoryService,
    SafetyGate,
    MemoryClassifier,
    PassiveExtractor
)
from app.memory.active_memory_handler import ActiveMemoryHandler
from app.storage.database import init_db, create_tables


class TestSafetyGate:
    """安全门闩测试"""
    
    def test_block_phone_number(self):
        """测试阻止手机号"""
        gate = SafetyGate()
        
        # 阻止手机号
        status, reason, _ = gate.check("我的手机号是13812345678")
        assert status == 'BLOCK'
        assert 'phone' in reason
    
    def test_block_phone_number_chinese_prefix(self):
        """测试阻止手机号（中文前缀，修复\b边界问题）"""
        gate = SafetyGate()
        
        # 中文前缀 + 手机号（之前的\b会失败）
        test_cases = [
            "住我的手机号是13812345678",
            "记住13912345678这个号码",
            "我手机13712345678",
            "电话是13612345678",
        ]
        
        for content in test_cases:
            status, reason, _ = gate.check(content)
            assert status == 'BLOCK', f"Should block: {content}"
            assert 'phone' in reason, f"Wrong reason for: {content}"
    
    def test_block_id_card(self):
        """测试阻止身份证号"""
        gate = SafetyGate()
        
        status, reason, _ = gate.check("我的身份证号是110101199001011234")
        assert status == 'BLOCK'
        assert 'id_card' in reason
    
    def test_block_password(self):
        """测试阻止密码"""
        gate = SafetyGate()
        
        status, reason, _ = gate.check("密码是abc123456")
        assert status == 'BLOCK'
        assert 'password' in reason
    
    def test_block_medical_record(self):
        """测试阻止病历"""
        gate = SafetyGate()
        
        status, reason, _ = gate.check("我的病历显示有高血压")
        assert status == 'BLOCK'
        assert 'medical_record' in reason
    
    def test_pass_normal_content(self):
        """测试正常内容通过"""
        gate = SafetyGate()
        
        status, reason, cleaned = gate.check("我家在北京市朝阳区")
        assert status == 'PASS'
        assert reason is None
        assert cleaned == "我家在北京市朝阳区"
    
    def test_pass_normal_digits(self):
        """测试正常数字内容通过（非PII）"""
        gate = SafetyGate()
        
        # 正常的数字不应该被误判
        test_cases = [
            "我住在朝阳区100号",
            "车牌号是京A12345",
            "房间号是1234",
        ]
        
        for content in test_cases:
            status, reason, cleaned = gate.check(content)
            assert status == 'PASS', f"Should pass: {content}"
            assert cleaned == content


class TestMemoryClassifier:
    """记忆分类器测试"""
    
    def test_classify_home_address(self):
        """测试分类家地址"""
        classifier = MemoryClassifier()
        
        category = classifier.classify("我家在北京市朝阳区")
        assert category == 'personal_basic'
    
    def test_classify_preference(self):
        """测试分类偏好"""
        classifier = MemoryClassifier()
        
        category = classifier.classify("我喜欢听周杰伦的歌")
        assert category == 'user_preference'
    
    def test_classify_restriction(self):
        """测试分类禁忌"""
        classifier = MemoryClassifier()
        
        category = classifier.classify("不要提及我的前公司")
        assert category == 'restrictions'
    
    def test_unclassifiable(self):
        """测试无法分类的内容"""
        classifier = MemoryClassifier()
        
        category = classifier.classify("今天天气真好")
        assert category is None


class TestPassiveExtractor:
    """被动提取器测试（PRD v1.24加权公式）"""
    
    def test_should_extract_preference_high_score(self):
        """测试应该提取偏好（高分）"""
        extractor = PassiveExtractor()
        
        should, score = extractor.should_extract(
            utterance="我平时喜欢听周杰伦",
            assistant_response="好的，已记录您的音乐偏好",
            context={}
        )
        
        # PRD v1.24: 高长期性(0.9) + 高稳定性(0.9) + 高个人(0.9)
        # score = 0.4*0.9 + 0.3*0.9 + 0.3*0.9 = 0.36 + 0.27 + 0.27 = 0.90
        assert should is True
        assert score >= 0.7  # 超过阈值0.7
        assert score >= 0.85  # 应该接近0.9
    
    def test_should_not_extract_temporary(self):
        """测试不应提取临时内容（低分）"""
        extractor = PassiveExtractor()
        
        should, score = extractor.should_extract(
            utterance="现在几点了",
            assistant_response="现在是下午3点",
            context={}
        )
        
        # 低长期性 + 低稳定性 + 低个人
        assert should is False
        assert score < 0.7
    
    def test_skip_one_shot_traffic(self):
        """测试跳过一次性交通查询（PRD v1.24）"""
        extractor = PassiveExtractor()
        
        test_cases = [
            "前面堵车吗",
            "路况怎么样",
            "现在拥堵吗",
        ]
        
        for utterance in test_cases:
            should, score = extractor.should_extract(
                utterance=utterance,
                assistant_response="",
                context={}
            )
            # 低长期性 → score应该低
            assert should is False, f"Should not extract: {utterance}"
            assert score < 0.7, f"Score too high for: {utterance}"
    
    def test_skip_next_intersection(self):
        """测试跳过下个路口查询（PRD v1.24）"""
        extractor = PassiveExtractor()
        
        should, score = extractor.should_extract(
            utterance="下个路口是哪里",
            assistant_response="",
            context={}
        )
        
        assert should is False
        assert score < 0.7
    
    def test_skip_ephemeral_state(self):
        """测试跳过临时车辆状态（PRD v1.24）"""
        extractor = PassiveExtractor()
        
        should, score = extractor.should_extract(
            utterance="当前车速是多少",
            assistant_response="",
            context={}
        )
        
        assert should is False
        assert score < 0.7
    
    def test_weighted_formula(self):
        """测试PRD v1.24加权公式：0.4*long + 0.3*stability + 0.3*personal"""
        extractor = PassiveExtractor()
        
        # 人工构造：高长期(0.9) + 高稳定(0.9) + 高个人(0.9)
        # "我经常在这条路上开" 包含 "我"(个人) + "经常"(长期+稳定)
        # score = 0.4*0.9 + 0.3*0.9 + 0.3*0.9 = 0.36 + 0.27 + 0.27 = 0.90
        should, score = extractor.should_extract(
            utterance="我经常在这条路上开",
            assistant_response="",
            context={}
        )
        
        # 应该超过阈值0.7
        assert should is True
        assert score >= 0.7, f"Score {score} should be >= 0.7"
        # 实际应该接近0.9
        assert score >= 0.85, f"Score {score} should be >= 0.85 for high long_term + stability + personal"
    
    def test_custom_threshold(self):
        """测试自定义阈值"""
        extractor = PassiveExtractor()
        
        should, score = extractor.should_extract(
            utterance="我喜欢这个",  # 中等分数
            assistant_response="",
            context={},
            threshold=0.9  # 高阈值
        )
        
        # 分数可能不够高
        assert score < 0.9
        assert should is False
    
    def test_extract_facts(self):
        """测试提取事实"""
        extractor = PassiveExtractor()
        
        facts = extractor.extract_facts(
            utterance="我平时喜欢听周杰伦",
            response=""
        )
        
        assert len(facts) > 0
        assert any("喜欢" in fact and "周杰伦" in fact for fact in facts)

    def test_extract_with_meta_passes_llm_category(self):
        """被动 LLM 必须带回 category，供 put_memory 使用（禁止写死 None）"""
        from unittest.mock import Mock

        mock_llm = Mock()
        mock_llm.extract_facts = Mock(return_value={
            "facts": ["喜欢听周杰伦"],
            "category": "user_preference",
        })
        extractor = PassiveExtractor(llm_client=mock_llm)
        meta = extractor.extract_with_meta("我平时喜欢听周杰伦", "好的")

        assert meta["facts"] == ["喜欢听周杰伦"]
        assert meta["category"] == "user_preference"
        assert meta["llm_result"]["category"] == "user_preference"
        mock_llm.extract_facts.assert_called_once()


class TestActiveMemoryHandler:
    """主动记忆处理器测试"""
    
    def test_detect_active_intent(self):
        """测试检测主动记忆意图"""
        content = ActiveMemoryHandler.detect_active_intent("帮我记住我家在北京市朝阳区")
        assert content is not None
        assert "我家在北京市朝阳区" in content
    
    def test_no_active_intent(self):
        """测试无主动意图"""
        content = ActiveMemoryHandler.detect_active_intent("今天天气真好")
        assert content is None
    
    def test_parse_home_address(self):
        """测试解析家地址"""
        parsed = ActiveMemoryHandler.parse_memory_content("我家在北京市朝阳区")
        
        assert parsed['is_home_address'] is True
        assert "家地址：" in parsed['normalized_content']
    
    def test_parse_company_address(self):
        """测试解析公司地址"""
        parsed = ActiveMemoryHandler.parse_memory_content("公司在北京市海淀区")
        
        assert parsed['is_company_address'] is True
        assert "公司地址：" in parsed['normalized_content']


class TestP2MemoryService:
    """P2记忆服务集成测试（需要数据库）"""
    
    @pytest.fixture(autouse=True)
    def setup_db(self):
        """设置测试数据库"""
        # 使用环境变量DATABASE_URL，如果没有则使用默认值
        # 允许CI/容器环境覆盖
        if 'DATABASE_URL' not in os.environ:
            os.environ['DATABASE_URL'] = 'postgresql://cockpit:cockpit@postgres:5432/cockpit_agent_test'
        
        try:
            init_db()
            create_tables()
        except Exception as e:
            pytest.skip(f"Database not available: {e}")
    
    def test_put_memory_home_address(self):
        """M-H1: 记住家地址（仅content，无坐标）"""
        service = P2MemoryService(enable_vector=False)
        
        user_id = "account_test:driver_001"
        memory_id = service.put_memory(
            user_id=user_id,
            content="家地址：北京市朝阳区xx路xx号",
            source_ref="test:active",
            is_active=True
        )
        
        assert memory_id is not None
        
        # 验证可解析地址
        addresses = service.parse_home_company_address(user_id)
        assert addresses['home'] is not None
        assert "北京市朝阳区" in addresses['home']
        
        # 清理
        service.delete_memory(memory_id, user_id)
    
    def test_block_pii(self):
        """M-B1: PII黑名单阻止"""
        service = P2MemoryService(enable_vector=False)
        
        user_id = "account_test:driver_002"
        memory_id = service.put_memory(
            user_id=user_id,
            content="我的手机号是13812345678",
            source_ref="test:blocked",
            is_active=True
        )
        
        # 应该被阻止
        assert memory_id is None
    
    def test_passive_preference_extraction(self):
        """M-P1: 被动偏好提取"""
        service = P2MemoryService(enable_vector=False)
        
        user_id = "account_test:driver_003"
        
        # 模拟被动提取
        service.handle_passive_extraction(
            user_id=user_id,
            utterance="我平时喜欢听周杰伦",
            assistant_response="好的",
            context={}
        )
        
        # 验证记忆被写入
        memories = service.list_memories(user_id)
        assert len(memories) > 0
        assert any("周杰伦" in m['content'] for m in memories)
        
        # 清理
        service.clear_user_memories(user_id)
    
    def test_driver_isolation(self):
        """M-D1: 驾驶员隔离"""
        service = P2MemoryService(enable_vector=False)
        
        user_id_1 = "account_test:driver_004"
        user_id_2 = "account_test:driver_005"
        
        # 驾驶员1的记忆
        memory_id_1 = service.put_memory(
            user_id=user_id_1,
            content="家地址：北京市朝阳区",
            source_ref="test:driver1",
            is_active=True
        )
        
        # 驾驶员2的记忆
        memory_id_2 = service.put_memory(
            user_id=user_id_2,
            content="家地址：上海市浦东新区",
            source_ref="test:driver2",
            is_active=True
        )
        
        # 验证隔离
        memories_1 = service.list_memories(user_id_1)
        memories_2 = service.list_memories(user_id_2)
        
        assert any("北京" in m['content'] for m in memories_1)
        assert not any("上海" in m['content'] for m in memories_1)
        
        assert any("上海" in m['content'] for m in memories_2)
        assert not any("北京" in m['content'] for m in memories_2)
        
        # 清理
        service.delete_memory(memory_id_1, user_id_1)
        service.delete_memory(memory_id_2, user_id_2)


class TestNavigationIntegration:
    """导航集成测试（mock memory service，无需Postgres）"""
    
    @pytest.mark.asyncio
    async def test_nav_home_from_memory(self):
        """M-N1: 导航回家（从content获取地址文本，云端不返回坐标）"""
        from app.adapters.navigation import NavigationAdapter
        from unittest.mock import Mock, MagicMock
        
        # Mock P2MemoryService
        mock_memory_service = Mock()
        mock_memory_service.parse_home_company_address = MagicMock(return_value={
            'home': '北京市朝阳区望京SOHO T1',
            'company': None
        })
        
        # 创建导航适配器（注入mock service）
        adapter = NavigationAdapter(p2_memory_service=mock_memory_service)
        
        user_id = "account_test:driver_006"
        
        # 解析"家"
        poi_data = await adapter.resolve_poi("家", user_id=user_id)
        
        # 验证POI数据
        assert poi_data is not None
        assert poi_data['poi_name'] == '家'
        
        # 云端架构：只返回地址文本，不返回坐标（车端自行geocode）
        assert poi_data['address'] == '北京市朝阳区望京SOHO T1'
        assert poi_data['latitude'] == 0.0
        assert poi_data['longitude'] == 0.0
        
        # 验证memory service被正确调用
        mock_memory_service.parse_home_company_address.assert_called_once_with(user_id)
        
        # 验证execute返回的下行消息包含address_text
        from app.schemas.taskgraph import Step, DomainType, NavigationAction, NavGoal, RoutePreferences
        step = Step(
            step_id="step_1",
            domain=DomainType.NAVIGATION,
            action=NavigationAction(
                action="set_nav_goal",
                goal=NavGoal(**poi_data),
                route_prefs=RoutePreferences()
            ),
            description="导航到家"
        )
        
        result = await adapter.execute(step, {})
        
        # 验证下行消息格式（家/公司使用address_text）
        assert result['goal']['type'] == '家'
        assert result['goal']['address_text'] == '北京市朝阳区望京SOHO T1'
        assert 'latitude' not in result['goal']  # 云端不下发坐标
        assert 'longitude' not in result['goal']
    
    @pytest.mark.asyncio
    async def test_nav_home_not_in_memory(self):
        """测试导航回家但memory中没有地址（应返回None，让planner询问用户）"""
        from app.adapters.navigation import NavigationAdapter
        from unittest.mock import Mock, MagicMock
        
        # Mock P2MemoryService - 返回空地址
        mock_memory_service = Mock()
        mock_memory_service.parse_home_company_address = MagicMock(return_value={
            'home': None,
            'company': None
        })
        
        adapter = NavigationAdapter(p2_memory_service=mock_memory_service)
        
        user_id = "account_test:driver_007"
        
        # 解析"家"应该返回None（因为memory中没有）
        poi_data = await adapter.resolve_poi("家", user_id=user_id)
        
        assert poi_data is None  # 没有mock fallback，必须返回None
        
        # 验证memory service被调用
        mock_memory_service.parse_home_company_address.assert_called_once_with(user_id)
    
    @pytest.mark.asyncio
    async def test_nav_company_from_memory(self):
        """测试导航到公司（从memory获取地址）"""
        from app.adapters.navigation import NavigationAdapter
        from unittest.mock import Mock, MagicMock
        
        # Mock P2MemoryService
        mock_memory_service = Mock()
        mock_memory_service.parse_home_company_address = MagicMock(return_value={
            'home': None,
            'company': '北京市海淀区中关村软件园'
        })
        
        adapter = NavigationAdapter(p2_memory_service=mock_memory_service)
        
        user_id = "account_test:driver_008"
        
        # 解析"公司"
        poi_data = await adapter.resolve_poi("公司", user_id=user_id)
        
        # 验证POI数据
        assert poi_data is not None
        assert poi_data['poi_name'] == '公司'
        assert poi_data['address'] == '北京市海淀区中关村软件园'
        assert poi_data['latitude'] == 0.0
        assert poi_data['longitude'] == 0.0
    
    @pytest.mark.asyncio
    async def test_nav_home_from_full_utterance(self):
        """测试导航回家（用户说完整话语如"导航回家"）- 回归测试"""
        from app.adapters.navigation import NavigationAdapter
        from unittest.mock import Mock, MagicMock
        
        # Mock P2MemoryService
        mock_memory_service = Mock()
        mock_memory_service.parse_home_company_address = MagicMock(return_value={
            'home': '北京市朝阳区望京SOHO',
            'company': None
        })
        
        adapter = NavigationAdapter(p2_memory_service=mock_memory_service)
        user_id = "account_default:driver1"
        
        # 测试各种包含"回家"的完整话语
        test_utterances = [
            "导航回家",
            "帮我导航回家",
            "我要回家",
            "带我回家",
            "导航到家",
            "去家里"
        ]
        
        for utterance in test_utterances:
            poi_data = await adapter.resolve_poi(utterance, user_id=user_id)
            
            # 验证POI数据
            assert poi_data is not None, f"Failed to resolve for utterance: {utterance}"
            assert poi_data['poi_name'] == '家', f"Wrong poi_name for utterance: {utterance}"
            assert poi_data['address'] == '北京市朝阳区望京SOHO', f"Wrong address for utterance: {utterance}"
            assert poi_data['latitude'] == 0.0
            assert poi_data['longitude'] == 0.0


class TestEmbeddingClientAPI:
    """测试Embedding客户端API（OpenAI v1+ SDK）- 回归测试"""
    
    def test_embedding_client_initialization(self):
        """测试QwenEmbedding初始化使用新的OpenAI客户端"""
        from app.memory.qwen_clients import QwenEmbedding
        
        # 初始化客户端
        client = QwenEmbedding(
            api_key="test_key",
            api_base="https://test.api.com/v1"
        )
        
        # 验证客户端属性
        assert hasattr(client, 'client'), "QwenEmbedding should have 'client' attribute"
        assert client.model == 'text-embedding-v3'
        assert client.dimension == 1024
        assert client.api_base == "https://test.api.com/v1"
        assert client.api_key == "test_key"
    
    def test_embedding_api_call_shape(self):
        """测试embedding API调用返回正确的向量维度（mock测试）"""
        from app.memory.qwen_clients import QwenEmbedding
        from unittest.mock import Mock, MagicMock
        
        client = QwenEmbedding()
        
        # Mock OpenAI client的embeddings.create方法
        mock_response = Mock()
        mock_embedding_obj = Mock()
        mock_embedding_obj.embedding = [0.1] * 1024  # 1024维向量
        mock_response.data = [mock_embedding_obj]
        
        client.client.embeddings.create = MagicMock(return_value=mock_response)
        
        # 调用embed方法
        result = client.embed("测试文本")
        
        # 验证结果
        assert result is not None, "Embedding should not be None"
        assert len(result) == 1024, f"Expected 1024 dimensions, got {len(result)}"
        assert all(isinstance(x, (int, float)) for x in result), "All elements should be numbers"
        
        # 验证client.embeddings.create被调用
        client.client.embeddings.create.assert_called_once()
        call_args = client.client.embeddings.create.call_args
        assert call_args[1]['model'] == 'text-embedding-v3'
        assert call_args[1]['input'] == '测试文本'
        assert call_args[1]['dimensions'] == 1024
    
    def test_embedding_empty_text_returns_none(self):
        """测试空文本返回None（不生成0维向量）"""
        from app.memory.qwen_clients import QwenEmbedding
        
        client = QwenEmbedding()
        
        # 空文本应该返回None
        assert client.embed("") is None
        assert client.embed("   ") is None
        assert client.embed(None) is None
    
    def test_embedding_invalid_dimension_returns_none(self):
        """测试错误维度的embedding返回None"""
        from app.memory.qwen_clients import QwenEmbedding
        from unittest.mock import Mock, MagicMock
        
        client = QwenEmbedding()
        
        # Mock返回错误维度（例如0维或512维）
        mock_response = Mock()
        mock_embedding_obj = Mock()
        mock_embedding_obj.embedding = []  # 0维向量
        mock_response.data = [mock_embedding_obj]
        
        client.client.embeddings.create = MagicMock(return_value=mock_response)
        
        # 调用embed方法
        result = client.embed("测试文本")
        
        # 验证返回None（不存储0维向量）
        assert result is None, "Should return None for 0-dimension embedding"
    
    def test_chat_completion_client_initialization(self):
        """测试QwenMemoryExtractor初始化使用新的OpenAI客户端"""
        from app.memory.qwen_clients import QwenMemoryExtractor
        
        # 初始化客户端
        client = QwenMemoryExtractor(
            api_key="test_key",
            api_base="https://test.api.com/v1"
        )
        
        # 验证客户端属性
        assert hasattr(client, 'client'), "QwenMemoryExtractor should have 'client' attribute"
        assert client.model == 'qwen-turbo'
        assert client.api_base == "https://test.api.com/v1"
        assert client.api_key == "test_key"


class TestPassiveQueuePRDv127:
    """PRD v1.27 被动记忆队列测试"""
    
    def test_enqueue_candidate(self):
        """测试入队候选"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue
        from unittest.mock import Mock
        
        # Mock Redis客户端
        mock_redis = Mock()
        mock_redis.rpush = Mock(return_value=1)
        mock_redis.expire = Mock(return_value=True)
        
        queue = PassiveMemoryCandidateQueue(mock_redis)
        
        # 入队候选
        success = queue.enqueue_candidate(
            user_id="account_test:driver_001",
            session_id="session_001",
            utterance="我平时喜欢听周杰伦",
            assistant_response="好的",
            context={},
            trace_id="trace_001"
        )
        
        assert success is True
        mock_redis.rpush.assert_called_once()
        mock_redis.expire.assert_called_once()
    
    def test_get_all_candidates(self):
        """测试获取所有候选"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue
        from unittest.mock import Mock
        import json
        
        # Mock Redis客户端
        mock_redis = Mock()
        candidate_data = {
            "session_id": "session_001",
            "utterance": "我平时喜欢听周杰伦",
            "assistant_response": "好的",
            "context": {},
            "trace_id": "trace_001",
            "timestamp": "2024-01-01T00:00:00"
        }
        mock_redis.lrange = Mock(return_value=[json.dumps(candidate_data, ensure_ascii=False).encode()])
        
        queue = PassiveMemoryCandidateQueue(mock_redis)
        
        # 获取候选
        candidates = queue.get_all_candidates("account_test:driver_001")
        
        assert len(candidates) == 1
        assert candidates[0]['utterance'] == "我平时喜欢听周杰伦"
        mock_redis.lrange.assert_called_once()
    
    def test_clear_candidates(self):
        """测试清空候选"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue
        from unittest.mock import Mock
        
        # Mock Redis客户端
        mock_redis = Mock()
        mock_redis.llen = Mock(return_value=3)
        mock_redis.delete = Mock(return_value=1)
        
        queue = PassiveMemoryCandidateQueue(mock_redis)
        
        # 清空候选
        count = queue.clear_candidates("account_test:driver_001")
        
        assert count == 3
        mock_redis.llen.assert_called_once()
        mock_redis.delete.assert_called_once()
    
    def test_consumer_consume_for_user(self):
        """测试消费者处理候选队列"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue, PassiveMemoryConsumer
        from unittest.mock import Mock, MagicMock
        
        # Mock队列
        mock_queue = Mock()
        mock_queue.get_all_candidates = MagicMock(return_value=[
            {
                "session_id": "session_001",
                "utterance": "我平时喜欢听周杰伦",
                "assistant_response": "好的",
                "context": {},
                "trace_id": "trace_001",
                "timestamp": "2024-01-01T00:00:00"
            }
        ])
        mock_queue.clear_candidates = Mock(return_value=1)
        
        # Mock P2MemoryService
        mock_p2_service = Mock()
        mock_p2_service.handle_passive_extraction = Mock()
        
        consumer = PassiveMemoryConsumer(mock_queue, mock_p2_service)
        
        # 消费候选
        result = consumer.consume_for_user(
            user_id="account_test:driver_001",
            trigger_reason="task_end_completed"
        )
        
        # 验证结果
        assert result['candidates_count'] == 1
        assert result['extracted_count'] == 1
        assert result['trigger'] == "task_end_completed"
        
        # 验证调用
        mock_queue.get_all_candidates.assert_called_once()
        mock_p2_service.handle_passive_extraction.assert_called_once()
        mock_queue.clear_candidates.assert_called_once()
    
    def test_consumer_no_candidates(self):
        """测试消费者处理空队列"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue, PassiveMemoryConsumer
        from unittest.mock import Mock, MagicMock
        
        # Mock空队列
        mock_queue = Mock()
        mock_queue.get_all_candidates = MagicMock(return_value=[])
        
        # Mock P2MemoryService
        mock_p2_service = Mock()
        
        consumer = PassiveMemoryConsumer(mock_queue, mock_p2_service)
        
        # 消费空队列
        result = consumer.consume_for_user(
            user_id="account_test:driver_001",
            trigger_reason="task_end_completed"
        )
        
        # 验证结果
        assert result['candidates_count'] == 0
        assert result['extracted_count'] == 0
        
        # 验证handle_passive_extraction未被调用
        mock_p2_service.handle_passive_extraction.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_per_turn_no_long_term_put(self):
        """PRD v1.27: 验证每轮对话不直接写入长期记忆（仅入队）"""
        from app.memory.passive_queue import PassiveMemoryCandidateQueue
        from unittest.mock import Mock
        
        # Mock Redis客户端
        mock_redis = Mock()
        mock_redis.rpush = Mock(return_value=1)
        mock_redis.expire = Mock(return_value=True)
        
        queue = PassiveMemoryCandidateQueue(mock_redis)
        
        # 模拟对话轮次：仅入队，不put
        queue.enqueue_candidate(
            user_id="account_test:driver_001",
            session_id="session_001",
            utterance="我平时喜欢听周杰伦",
            assistant_response="好的",
            context={},
            trace_id="trace_001"
        )
        
        # 验证仅调用了rpush（入队），未调用任何put操作
        mock_redis.rpush.assert_called_once()
        
        # 此时不应该有任何长期记忆写入（由consumer触发）
        # 这里只是验证队列操作正确
    
    def test_active_remember_still_sync_puts(self):
        """PRD v1.27: 验证主动记忆仍然同步写入（不经过队列）"""
        # 这个测试在test_put_memory_home_address中已覆盖
        # 主动记忆直接调用put_memory，不经过队列
        pass


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
