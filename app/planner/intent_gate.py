# -*- coding: utf-8 -*-
"""Ingress intent gate — rule short-circuit BEFORE LLM (PRD v1.11)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ForcedDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    CALENDAR = "calendar"
    CHITCHAT = "chitchat"
    MEDIA = "media"
    NAVIGATION = "navigation"
    VEHICLE = "vehicle"


@dataclass
class IntentDecision:
    domain: ForcedDomain
    reason: str
    chitchat_kind: Optional[str] = None  # joke | generic


# 手册 / 故障 / 操作说明 — 强制 knowledge
_KNOWLEDGE_PATTERNS = [
    r"(如何|怎么|怎样).{0,12}(使用|打开|关闭|操作|设置|开启)",
    r"(用户)?手册|说明书|用车指南",
    r"(是否|是不是).{0,8}(故障|正常|坏了)",
    r"(异响|噪音|报警|指示灯|故障灯|报错)",
    r"(轮胎|气压|保养|机油|保修|充电|续航).{0,10}(多久|怎么|如何|多少)",
    r"致享|车型手册",
]

# 日程 — 强制 calendar
_CALENDAR_PATTERNS = [
    r"(查询|查看|看看|有什么|还有).{0,8}(日程|行程|安排|会议|提醒)",
    r"(今天|明日|明天|本周|下周).{0,6}(日程|行程|安排|会议)",
    r"(今日行程|下一个会议|下个日程|下一个日程)",
    r"(创建|取消|添加).{0,6}(会议|日程|提醒)",
]

# 明确闲聊
_JOKE_PATTERNS = [
    r"(听|讲|说).{0,4}(笑话|段子)",
    r"笑话",
]


def classify_intent(utterance: str) -> Optional[IntentDecision]:
    """Return forced domain when rule matches; None = leave to LLM/rules."""
    text = (utterance or "").strip()
    if not text:
        return None

    for pat in _KNOWLEDGE_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.KNOWLEDGE, f"knowledge_gate:{pat}")

    for pat in _CALENDAR_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.CALENDAR, f"calendar_gate:{pat}")

    for pat in _JOKE_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.CHITCHAT, f"chitchat_joke:{pat}", chitchat_kind="joke")

    return None
