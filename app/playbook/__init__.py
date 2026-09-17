# -*- coding: utf-8 -*-
"""ACE Playbook - PRD v1.37 Feature 3

最小化Playbook存储（文件或PostgreSQL）：
- 在线：Assemble/Planner可读取静态快照（read-only）
- 离线：Generator/Reflector/Curator接口 + 脚本/作业追加Delta条目
- 合并：仅通过显式提升到快照（即使提升目前是CLI）
- 不混淆：与每轮长期记忆写入分离
"""

from .store import PlaybookStore, PlaybookEntry, PlaybookSnapshot
from .offline_pipeline import (
    PlaybookGenerator,
    PlaybookReflector,
    PlaybookCurator,
    PlaybookDelta
)

__all__ = [
    "PlaybookStore",
    "PlaybookEntry",
    "PlaybookSnapshot",
    "PlaybookGenerator",
    "PlaybookReflector",
    "PlaybookCurator",
    "PlaybookDelta",
]
