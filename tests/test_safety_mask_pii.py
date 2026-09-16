# -*- coding: utf-8 -*-
"""阶段3闸门：MASK + 中文 PII 加固。"""

from app.memory.p2_memory_service import SafetyGate


class TestChinesePIINeverStored:
    """手机号/身份证永不入库（纯 PII → BLOCK）。"""

    def test_plain_phone_blocked(self):
        status, reason, cleaned = SafetyGate.check("13812345678")
        assert status == "BLOCK"
        assert "phone" in reason
        assert cleaned is None

    def test_phone_with_chinese_prefix_blocked(self):
        for content in [
            "住我的手机号是13812345678",
            "记住13912345678这个号码",
            "我手机13712345678",
            "电话是13612345678",
        ]:
            status, reason, cleaned = SafetyGate.check(content)
            assert status == "BLOCK", content
            assert "phone" in reason
            assert cleaned is None

    def test_phone_variants_blocked(self):
        """变体：+86、空格、横线分隔仍须拦住。"""
        variants = [
            "+8613812345678",
            "86-13812345678",
            "138-1234-5678",
            "138 1234 5678",
            "我的号是 +86 138-1234-5678",
        ]
        for content in variants:
            status, reason, cleaned = SafetyGate.check(content)
            assert status == "BLOCK", f"should block: {content}"
            assert "phone" in reason
            assert cleaned is None

    def test_id_card_never_stored(self):
        status, reason, cleaned = SafetyGate.check("我的身份证号是110101199001011234")
        assert status == "BLOCK"
        assert "id_card" in reason
        assert cleaned is None


class TestMaskKeepsNonSensitiveFacts:
    """可脱敏场景：MASK 后只留非敏感事实。"""

    def test_mask_phone_keeps_preference(self):
        status, reason, cleaned = SafetyGate.check(
            "我平时喜欢听周杰伦，手机号是13812345678"
        )
        assert status == "MASK"
        assert "phone" in reason
        assert cleaned is not None
        assert "周杰伦" in cleaned
        assert "13812345678" not in cleaned
        assert "手机号" not in cleaned
        assert "138" not in cleaned or "喜欢" in cleaned

    def test_mask_id_keeps_home_address(self):
        status, reason, cleaned = SafetyGate.check(
            "家地址：北京市朝阳区望京，身份证110101199001011234"
        )
        assert status == "MASK"
        assert cleaned is not None
        assert "望京" in cleaned
        assert "110101199001011234" not in cleaned

    def test_mask_variant_phone_keeps_fact(self):
        status, reason, cleaned = SafetyGate.check(
            "喜欢听周杰伦，电话 +86 138-1234-5678"
        )
        assert status == "MASK"
        assert "周杰伦" in cleaned
        assert "138" not in cleaned or "喜欢" in cleaned
        assert "5678" not in cleaned

    def test_password_still_hard_block(self):
        status, reason, cleaned = SafetyGate.check("密码是abc123456，我也喜欢听歌")
        assert status == "BLOCK"
        assert "password" in reason
        assert cleaned is None

    def test_medical_still_hard_block(self):
        status, reason, cleaned = SafetyGate.check("我的病历显示有高血压，平时喜欢跑步")
        assert status == "BLOCK"
        assert "medical_record" in reason

    def test_normal_content_still_pass(self):
        status, reason, cleaned = SafetyGate.check("我家在北京市朝阳区")
        assert status == "PASS"
        assert cleaned == "我家在北京市朝阳区"
