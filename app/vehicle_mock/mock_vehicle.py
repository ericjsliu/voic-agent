#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mock Vehicle - MQTT subscriber/publisher"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional
import paho.mqtt.client as mqtt


class MockVehicle:
    """Mock车辆（仅接收orchestrator验证后的TaskGraph）"""
    
    # MQTT主题
    TOPIC_DOWNLINK = "cockpit/agent/taskgraph"
    TOPIC_UPLINK_WRITEBACK = "cockpit/agent/writeback"
    TOPIC_UPLINK_TELEMETRY = "cockpit/agent/telemetry"
    
    def __init__(
        self,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
        client_id: str = "mock_vehicle",
        model_id: str = "model_a",
        enable_nav_arrived: bool = False  # PRD v1.7: nav_arrived is optional (default off)
    ):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.client_id = client_id
        self.model_id = model_id
        self.enable_nav_arrived = enable_nav_arrived
        
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        
        # 车辆状态（扩展以支持L1 gate测试）
        self.vehicle_state = {
            "model_id": model_id,
            "gear": "P",  # P, R, N, D
            "speed_kmh": 0,
            "latitude": 39.9042,
            "longitude": 116.4074,
            # Windows & Openings
            "windows_status": "closed",  # open/closed/partial
            "sunroof_status": "closed",  # open/closed/partial
            "sunshade_status": "closed",
            # Doors & Security
            "doors_locked": True,
            "child_lock": False,
            "trunk_open": False,
            "frunk_open": False,
            "charge_port_open": False,
            # Climate
            "ac_on": False,
            "ac_temp": 24,
            "ac_fan_speed": 3,
            "ac_fan_mode": "both",
            "ac_circulation": "external",
            "defrost_front": False,
            "defrost_rear": False,
            # Comfort
            "seat_heat_level": 0,  # 0-3
            "seat_vent_level": 0,  # 0-3
            "steering_wheel_heat": False,
            # Lighting
            "ambient_light": False,
            "fog_light": False,
            "position_light": False,
            "low_beam": False,
            "headlights_on": False,
            # Mirrors & Wipers
            "mirrors_folded": False,
            "wiper_speed": 0,  # 0-5
        }
    
    def _on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            print(f"[MockVehicle] Connected to MQTT broker")
            # 订阅下行主题
            client.subscribe(self.TOPIC_DOWNLINK)
            print(f"[MockVehicle] Subscribed to {self.TOPIC_DOWNLINK}")
        else:
            print(f"[MockVehicle] Connection failed with code {rc}")
    
    def _on_message(self, client, userdata, msg):
        """消息回调"""
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            print(f"[MockVehicle] Received on {msg.topic}:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            
            # 处理TaskGraph下行
            if msg.topic == self.TOPIC_DOWNLINK:
                self._handle_taskgraph(payload)
        
        except Exception as e:
            print(f"[MockVehicle] Error handling message: {e}")
    
    def _handle_taskgraph(self, taskgraph: dict):
        """处理TaskGraph下行"""
        print(f"[MockVehicle] Processing TaskGraph...")
        
        # PRD v1.9 / detailed-v2.2: Extract trace_id for echoing in writebacks
        trace_id = taskgraph.get("trace_id")
        
        # P0 fix #1: Check if this is a confirmed L2 execute frame
        metadata = taskgraph.get("metadata", {})
        is_l2_confirmed = metadata.get("l2_confirmed", False)
        
        # Mock执行：遍历所有步骤，发送writeback
        for task in taskgraph.get("tasks", []):
            task_id = task.get("task_id")
            branch_id = task.get("branch_id", "main")
            
            for step in task.get("steps", []):
                step_id = step.get("step_id")
                domain = step.get("domain")
                action_data = step.get("action", {})
                action = action_data.get("action")
                level = action_data.get("level", "L0")
                
                print(f"[MockVehicle] Step {step_id}: {domain}.{action} (level={level})")
                
                # L2 handling: confirmed L2 executes, unconfirmed L2 waits
                if level == "L2":
                    if is_l2_confirmed:
                        # P0 fix #1: Confirmed L2 - execute and send vehicle_ack
                        print(f"[MockVehicle] Executing confirmed L2 step {step_id}")
                        # Fall through to execute like L0/L1
                    else:
                        # Unconfirmed L2 - requires explicit UI/API confirmation
                        print(f"[MockVehicle] L2 step {step_id} requires explicit confirmation (no auto-accept)")
                        continue
                
                # L0/L1: 立即发送ack (unified status: accepted/rejected/failed)
                if domain == "vehicle":
                    asyncio.create_task(
                        self._send_writeback(task_id, step_id, branch_id, trace_id, "vehicle_ack", "accepted", delay=0.5)
                    )
                elif domain == "navigation":
                    # PRD v1.7 / detailed-v2.0.1: Always emit nav_route_started (completes the step)
                    asyncio.create_task(
                        self._send_writeback(task_id, step_id, branch_id, trace_id, "nav_route_started", "accepted", delay=1.0)
                    )
                    
                    # nav_arrived is optional (default off) - only for demo purposes
                    if self.enable_nav_arrived:
                        asyncio.create_task(
                            self._send_writeback(task_id, step_id, branch_id, trace_id, "nav_arrived", "accepted", delay=8.0)
                        )
                elif domain == "media":
                    asyncio.create_task(
                        self._send_writeback(task_id, step_id, branch_id, trace_id, "media_ack", "accepted", delay=0.5)
                    )
                elif domain == "calendar":
                    asyncio.create_task(
                        self._send_writeback(task_id, step_id, branch_id, trace_id, "calendar_ack", "accepted", delay=0.5)
                    )
    
    async def _send_writeback(
        self,
        task_id: str,
        step_id: str,
        branch_id: str,
        trace_id: Optional[str],
        event: str,
        status: str,
        reason: Optional[str] = None,
        delay: float = 0.0
    ):
        """发送writeback（PRD v1.9: echo trace_id）"""
        if delay > 0:
            await asyncio.sleep(delay)
        
        writeback = {
            "task_id": task_id,
            "step_id": step_id,
            "branch_id": branch_id,
            "trace_id": trace_id,  # PRD v1.9 / detailed-v2.2: echo unchanged
            "event": event,
            "status": status,
            "reason": reason,
            "ts": datetime.utcnow().isoformat() + "Z"
        }
        
        payload = json.dumps(writeback, ensure_ascii=False)
        self.client.publish(self.TOPIC_UPLINK_WRITEBACK, payload)
        print(f"[MockVehicle] Sent writeback: {event} -> {status} (trace_id={trace_id})")
    
    async def _send_confirm_result(
        self,
        task_id: str,
        step_id: str,
        branch_id: str,
        trace_id: Optional[str],
        accepted: bool,
        delay: float = 0.0
    ):
        """发送L2确认结果（PRD v1.9: echo trace_id）"""
        if delay > 0:
            await asyncio.sleep(delay)
        
        status = "accepted" if accepted else "declined"
        await self._send_writeback(
            task_id, step_id, branch_id, trace_id,
            "confirm_result", status
        )
    
    async def publish_telemetry_loop(self, interval: float = 5.0):
        """定期发布遥测数据"""
        while True:
            telemetry = {
                "vehicle_id": "vehicle_001",
                "model_id": self.model_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "gear": self.vehicle_state["gear"],
                "speed_kmh": self.vehicle_state["speed_kmh"],
                "latitude": self.vehicle_state["latitude"],
                "longitude": self.vehicle_state["longitude"],
                "windows_status": self.vehicle_state["windows_status"],
                "doors_locked": self.vehicle_state["doors_locked"],
                "ac_on": self.vehicle_state["ac_on"],
                "ac_temp": self.vehicle_state["ac_temp"],
            }
            
            payload = json.dumps(telemetry, ensure_ascii=False)
            self.client.publish(self.TOPIC_UPLINK_TELEMETRY, payload)
            print(f"[MockVehicle] Published telemetry (model={self.model_id})")
            
            await asyncio.sleep(interval)
    
    def start(self):
        """启动Mock车辆"""
        print(f"[MockVehicle] Connecting to {self.mqtt_broker}:{self.mqtt_port}")
        self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
        self.client.loop_start()
    
    def stop(self):
        """停止Mock车辆"""
        self.client.loop_stop()
        self.client.disconnect()
        print(f"[MockVehicle] Disconnected")


async def main():
    """主函数"""
    mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    model_id = sys.argv[1] if len(sys.argv) > 1 else "model_a"
    
    # PRD v1.7: nav_arrived is optional (default off)
    enable_nav_arrived = os.getenv("MOCK_ENABLE_NAV_ARRIVED", "false").lower() == "true"
    
    vehicle = MockVehicle(
        mqtt_broker,
        mqtt_port,
        model_id=model_id,
        enable_nav_arrived=enable_nav_arrived
    )
    print(f"[MockVehicle] Starting with model: {model_id}, nav_arrived: {enable_nav_arrived}")
    vehicle.start()
    
    # 启动遥测发布
    try:
        await vehicle.publish_telemetry_loop()
    except KeyboardInterrupt:
        print("\n[MockVehicle] Shutting down...")
        vehicle.stop()


if __name__ == "__main__":
    asyncio.run(main())
