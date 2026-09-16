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
    """被动提取器测试"""
    
    def test_should_extract_preference(self):
        """测试应该提取偏好"""
        extractor = PassiveExtractor()
        
        should, confidence = extractor.should_extract(
            utterance="我平时喜欢听周杰伦",
            assistant_response="好的，已记录您的音乐偏好",
            context={}
        )
        
        assert should is True
        assert confidence > 0.3
    
    def test_should_not_extract_temporary(self):
        """测试不应提取临时内容"""
        extractor = PassiveExtractor()
        
        should, confidence = extractor.should_extract(
            utterance="现在几点了",
            assistant_response="现在是下午3点",
            context={}
        )
        
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
        # 使用测试数据库
        os.environ['DATABASE_URL'] = 'postgresql://cockpit:cockpit@localhost:5432/cockpit_agent_test'
        
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
    """导航集成测试"""
    
    @pytest.mark.asyncio
    async def test_nav_home_from_memory(self):
        """M-N1: 导航回家（从content解析地址）"""
        from app.adapters.navigation import NavigationAdapter
        
        # 创建P2服务并存储家地址
        service = P2MemoryService(enable_vector=False)
        user_id = "account_test:driver_006"
        
        service.put_memory(
            user_id=user_id,
            content="家地址：北京市朝阳区",
            source_ref="test:nav",
            is_active=True
        )
        
        # 创建导航适配器
        adapter = NavigationAdapter(p2_memory_service=service)
        
        # 解析"家"
        poi_data = await adapter.resolve_poi("家", user_id=user_id)
        
        assert poi_data is not None
        assert poi_data['poi_name'] == '家'
        assert 'latitude' in poi_data
        assert 'longitude' in poi_data
        # 地址应该是从memory读取的
        assert "北京市朝阳区" in poi_data.get('address', '')
        
        # 清理
        service.clear_user_memories(user_id)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
