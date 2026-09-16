# -*- coding: utf-8 -*-
"""主动记忆意图处理器

处理显式记忆指令：
- "帮我记住..."
- "记下..."
- "保存..."
"""

import re
from typing import Optional, Dict, Any


class ActiveMemoryHandler:
    """主动记忆指令识别与处理"""
    
    # 主动记忆触发词
    ACTIVE_TRIGGERS = [
        "帮我记", "记住", "记下", "保存", "记一下", 
        "帮忙记", "请记住", "给我记", "存一下"
    ]
    
    @classmethod
    def detect_active_intent(cls, utterance: str) -> Optional[str]:
        """检测是否是主动记忆指令
        
        Returns:
            要记忆的内容（不含触发词本身），或None
        """
        for trigger in cls.ACTIVE_TRIGGERS:
            if trigger in utterance:
                # 提取触发词后的内容
                parts = utterance.split(trigger, 1)
                if len(parts) == 2:
                    content = parts[1].strip()
                    # 去除可能的"了"、"啊"等语气词
                    content = re.sub(r'^[了啊吧呢]', '', content).strip()
                    
                    if content:
                        return content
        
        return None
    
    @classmethod
    def should_skip_confirmation(cls, content: str) -> bool:
        """判断是否需要跳过确认（按PRD直接写入）
        
        P2设计：主动记忆无需确认，直接写入（过安全门闩后）
        
        Returns:
            True（总是跳过确认）
        """
        return True
    
    @classmethod
    def parse_memory_content(cls, content: str) -> Dict[str, Any]:
        """解析记忆内容，提取结构化信息
        
        Returns:
            {
                'raw_content': str,
                'is_home_address': bool,
                'is_company_address': bool,
                'normalized_content': str
            }
        """
        result = {
            'raw_content': content,
            'is_home_address': False,
            'is_company_address': False,
            'normalized_content': content
        }
        
        # 检测家地址
        if any(kw in content for kw in ['家', '家里', '我家', '家庭地址']):
            if any(kw in content for kw in ['在', '是', '地址']):
                result['is_home_address'] = True
                # 归一化为"家地址：xxx"格式
                addr = re.sub(r'.*(?:家|我家|家庭地址)(?:在|是|地址[是在]?)', '', content).strip()
                result['normalized_content'] = f"家地址：{addr}"
        
        # 检测公司地址
        if any(kw in content for kw in ['公司', '单位', '工作地点']):
            if any(kw in content for kw in ['在', '是', '地址']):
                result['is_company_address'] = True
                addr = re.sub(r'.*(?:公司|单位|工作地点)(?:在|是|地址[是在]?)', '', content).strip()
                result['normalized_content'] = f"公司地址：{addr}"
        
        return result


def create_active_memory_response(memory_id: str, content: str) -> str:
    """生成记忆成功的回复文本
    
    Args:
        memory_id: 记忆ID
        content: 记忆内容
        
    Returns:
        回复文本
    """
    # 简洁确认
    if "地址" in content:
        return "好的，已记住这个地址"
    elif "喜欢" in content or "偏好" in content:
        return "好的，已记住您的偏好"
    else:
        return "好的，已记住"
