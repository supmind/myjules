from dataclasses import dataclass, field
from typing import List

# New file added by MiniJules
@dataclass
class TaskState:
    """封装与单个任务相关的所有状态。"""
    task_string: str
    plan: str = ""
    plan_approved: bool = False
    current_step_index: int = 0
    work_history: List[str] = field(default_factory=list)