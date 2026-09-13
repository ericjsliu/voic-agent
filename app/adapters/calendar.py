# -*- coding: utf-8 -*-
"""Calendar domain adapter — on-demand calendar service (P0 mock)."""

from datetime import datetime
from typing import Dict, Any, Optional, List
from .base import BaseDomainAdapter
from ..schemas.taskgraph import Step, CalendarAction


class CalendarAdapter(BaseDomainAdapter):
    """日历适配器：按需查云端/mock 日程，不依赖 telemetry 周期上报。"""

    def __init__(self):
        now = datetime.now()
        self._mock_events: List[Dict[str, Any]] = [
            {
                "title": "产品周会",
                "start": now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(timespec="minutes"),
                "end": now.replace(hour=11, minute=0, second=0, microsecond=0).isoformat(timespec="minutes"),
                "location": "线上",
            },
            {
                "title": "座舱方案评审",
                "start": now.replace(hour=15, minute=30, second=0, microsecond=0).isoformat(timespec="minutes"),
                "end": now.replace(hour=16, minute=30, second=0, microsecond=0).isoformat(timespec="minutes"),
                "location": "会议室 A",
            },
        ]

    async def validate(self, step: Step, shadow_state: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        action: CalendarAction = step.action
        if action.action in ("create_event", "add_event"):
            if not action.event_title:
                return False, "add_event requires event_title"
            if not action.start_time:
                return False, "add_event requires start_time"
        return True, None

    async def execute(self, step: Step, context: Dict[str, Any]) -> Dict[str, Any]:
        action: CalendarAction = step.action
        result: Dict[str, Any] = {
            "step_id": step.step_id,
            "domain": "calendar",
            "action": action.action,
            "status": "success",
            "mqtt": False,
        }

        if action.action in ("query_events", "query_schedule", "today_schedule", "next_appointment"):
            events = list(self._mock_events)
            if action.action == "next_appointment" and events:
                events = [events[0]]
            lines = [
                f"{e['start'][11:16]} {e['title']}（{e.get('location') or '无地点'}）"
                for e in events
            ]
            spoken = ("今天的日程：" + "；".join(lines)) if lines else "今天没有日程安排。"
            result["events"] = events
            result["response"] = spoken
            result["answer"] = spoken
            return result

        if action.action in ("create_event", "add_event"):
            result["event_title"] = action.event_title
            result["start_time"] = action.start_time
            result["end_time"] = action.end_time
            result["location"] = action.location
            result["response"] = f"已创建日程：{action.event_title}"
            result["mqtt"] = True
            return result

        if action.action == "cancel_event":
            result["response"] = "已取消相关日程（mock）。"
            result["mqtt"] = True
            return result

        result["status"] = "pending"
        return result
