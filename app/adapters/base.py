# -*- coding: utf-8 -*-
"""Base domain adapter"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from ..schemas.taskgraph import Step


class BaseDomainAdapter(ABC):
    """领域适配器基类"""
    
    @abstractmethod
    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证步骤是否可执行
        
        Args:
            step: 任务步骤
            shadow_state: 影子状态
            
        Returns:
            (是否有效, 错误原因)
        """
        pass
    
    @abstractmethod
    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤
        
        Args:
            step: 任务步骤
            context: 执行上下文
            
        Returns:
            执行结果
        """
        pass
