# -*- coding: utf-8 -*-
"""Capability Profile Loader"""

import os
import json
from typing import Dict, Optional
from pathlib import Path

from .schema import CapabilityProfile


class CapabilityLoader:
    """能力档案加载器"""
    
    def __init__(self, profiles_dir: Optional[str] = None):
        if profiles_dir is None:
            # 默认profiles目录
            profiles_dir = Path(__file__).parent
        self.profiles_dir = Path(profiles_dir)
        self._cache: Dict[str, CapabilityProfile] = {}
        self._load_all_profiles()
    
    def _load_all_profiles(self):
        """预加载所有profile文件"""
        for json_file in self.profiles_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    profile = CapabilityProfile(**data)
                    self._cache[profile.model_id] = profile
                    print(f"[CapabilityLoader] Loaded profile: {profile.model_id} - {profile.model_name}")
            except Exception as e:
                print(f"[CapabilityLoader] Failed to load {json_file}: {e}")
    
    def get_profile(self, model_id: str, hardware_option: Optional[str] = None) -> Optional[CapabilityProfile]:
        """获取能力档案
        
        Args:
            model_id: 车型ID
            hardware_option: 硬件选项（可选）
            
        Returns:
            CapabilityProfile or None
        """
        # 简化版：只按model_id查找，不考虑hardware_option细分
        # 生产环境可扩展为 model_id + hardware_option 的组合查找
        profile = self._cache.get(model_id)
        
        if profile:
            print(f"[CapabilityLoader] Retrieved profile for {model_id}: {len(profile.supported_actions)} actions")
        else:
            print(f"[CapabilityLoader] Profile not found for {model_id}")
        
        return profile
    
    def list_available_models(self) -> list[str]:
        """列出所有可用车型"""
        return list(self._cache.keys())
    
    def get_default_profile(self) -> Optional[CapabilityProfile]:
        """获取默认档案（model_a）"""
        return self.get_profile("model_a")


# 全局单例
_loader: Optional[CapabilityLoader] = None


def get_capability_loader() -> CapabilityLoader:
    """获取全局能力档案加载器"""
    global _loader
    if _loader is None:
        _loader = CapabilityLoader()
    return _loader
