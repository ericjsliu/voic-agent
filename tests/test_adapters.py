# -*- coding: utf-8 -*-
"""测试Domain Adapters"""

import pytest

from app.adapters import VehicleAdapter, NavigationAdapter
from app.schemas import Step, DomainType, VehicleAction, NavigationAction, NavGoal, ActionLevel


@pytest.mark.asyncio
async def test_vehicle_adapter_validate_gear():
    """测试车辆适配器：档位验证"""
    adapter = VehicleAdapter()
    
    # 档位在P，door_lock应该通过
    shadow_state = {"gear": "P", "speed_kmh": 0}
    step = Step(
        step_id="step_1",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="door_lock",
            target="all_doors",
            level=ActionLevel.L2
        )
    )
    
    is_valid, error = await adapter.validate(step, shadow_state)
    assert is_valid is True
    assert error is None
    
    # 档位不在P，door_lock应该失败
    shadow_state_d = {"gear": "D", "speed_kmh": 30}
    is_valid, error = await adapter.validate(step, shadow_state_d)
    assert is_valid is False
    assert "gear" in error.lower()


@pytest.mark.asyncio
async def test_vehicle_adapter_validate_temp():
    """测试车辆适配器：温度验证"""
    adapter = VehicleAdapter()
    
    # 有效温度
    step_valid = Step(
        step_id="step_1",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="ac_set_temp",
            value=24.0,
            level=ActionLevel.L1
        )
    )
    
    is_valid, error = await adapter.validate(step_valid, {})
    assert is_valid is True
    
    # 无效温度（过高）
    step_invalid = Step(
        step_id="step_2",
        domain=DomainType.VEHICLE,
        action=VehicleAction(
            action="ac_set_temp",
            value=35.0,
            level=ActionLevel.L1
        )
    )
    
    is_valid, error = await adapter.validate(step_invalid, {})
    assert is_valid is False
    assert "out of range" in error


@pytest.mark.asyncio
async def test_navigation_adapter_resolve_poi():
    """测试导航适配器：POI解析"""
    adapter = NavigationAdapter()
    
    # 解析已知POI
    poi_data = await adapter.resolve_poi("家")
    assert poi_data is not None
    assert poi_data["poi_name"] == "家"
    assert "latitude" in poi_data
    assert "longitude" in poi_data
    
    # 解析未知POI
    poi_data_unknown = await adapter.resolve_poi("火星基地")
    assert poi_data_unknown is None

    # 地图工具吃整句，Planner 不再切词
    home = await adapter.resolve_poi("导航回家")
    assert home is not None and home["poi_name"] == "家"
    airport = await adapter.resolve_poi("导航到机场")
    assert airport is not None and airport["poi_name"] == "机场"


@pytest.mark.asyncio
async def test_navigation_adapter_validate():
    """测试导航适配器：验证导航步骤"""
    adapter = NavigationAdapter()
    
    # 有效的导航步骤
    step = Step(
        step_id="step_1",
        domain=DomainType.NAVIGATION,
        action=NavigationAction(
            action="nav_to",
            goal=NavGoal(
                poi_name="公司",
                latitude=39.9163,
                longitude=116.3971
            )
        )
    )
    
    is_valid, error = await adapter.validate(step, {})
    assert is_valid is True
    
    # 无效：缺少goal
    step_invalid = Step(
        step_id="step_2",
        domain=DomainType.NAVIGATION,
        action=NavigationAction(
            action="nav_to",
            goal=None
        )
    )
    
    is_valid, error = await adapter.validate(step_invalid, {})
    assert is_valid is False
