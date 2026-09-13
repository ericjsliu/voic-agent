# -*- coding: utf-8 -*-
"""Domain adapters"""

from .base import BaseDomainAdapter
from .vehicle import VehicleAdapter
from .navigation import NavigationAdapter
from .media import MediaAdapter
from .calendar import CalendarAdapter
from .knowledge import KnowledgeAdapter
from .chitchat import ChitchatAdapter

__all__ = [
    "BaseDomainAdapter",
    "VehicleAdapter",
    "NavigationAdapter",
    "MediaAdapter",
    "CalendarAdapter",
    "KnowledgeAdapter",
    "ChitchatAdapter",
]
