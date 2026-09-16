# -*- coding: utf-8 -*-
"""意图规则闸：LLM 规划失败时的兜底，以及 LLM 出图后的域护栏（PRD v1.11 / 路线 C）。

不再在 Ingress 抢先短路 LLM。classify_intent 只用于：
1. 无模型 / 规划失败时的规则兜底
2. 手册/日程被误判闲聊时的事后纠偏
"""

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


# 导航 — 强制 navigation（单意图强特征）
_NAV_PATTERNS = [
    r"导航(到|去|回家|回公司)",
    r"取消导航",
    r"(途经|经由).{0,8}",
    r"(还有多久|多久到达|剩余距离|下一步怎么走)",
]

# 媒体 — 强制 media
_MEDIA_PATTERNS = [
    r"(播放|暂停|下一首|上一首).{0,12}(音乐|歌|电台|收藏|歌单)?",
    r"(音量|静音|大声|小声)",
    r"切换蓝牙",
]

# 车控单意图（不含混合「同时」）
_VEHICLE_PATTERNS = [
    r"^(打开|关闭).{0,6}(车窗|天窗|空调|雾灯|近光|氛围灯)",
    r"^(锁车|解锁|打开后备箱|打开前备箱)",
    r"^(座椅加热|座椅通风|方向盘加热|后视镜折叠|雨刮)",
]

# 明确闲聊
_JOKE_PATTERNS = [
    r"(听|讲|说).{0,4}(笑话|段子)",
    r"笑话",
]


def classify_intent(utterance: str) -> Optional[IntentDecision]:
    """强特征域判断。供规则兜底与事后护栏使用，不替代 LLM 规划。"""
    text = (utterance or "").strip()
    if not text:
        return None

    # 混合意图：不在此短路，交给 LLM/规则多 step
    if re.search(r"同时|并且|，.*?(播放|导航|打开|关闭)", text) and len(re.findall(r"打开|关闭|播放|导航|锁", text)) >= 2:
        return None

    for pat in _KNOWLEDGE_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.KNOWLEDGE, f"knowledge_gate:{pat}")

    for pat in _CALENDAR_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.CALENDAR, f"calendar_gate:{pat}")

    for pat in _NAV_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.NAVIGATION, f"nav_gate:{pat}")

    for pat in _MEDIA_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.MEDIA, f"media_gate:{pat}")

    for pat in _VEHICLE_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.VEHICLE, f"vehicle_gate:{pat}")

    for pat in _JOKE_PATTERNS:
        if re.search(pat, text):
            return IntentDecision(ForcedDomain.CHITCHAT, f"chitchat_joke:{pat}", chitchat_kind="joke")

    return None
