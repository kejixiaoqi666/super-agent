"""执行层：命令沙箱（超时/回收/幂等/危险门禁）。"""
from .sandbox import CommandError, Sandbox

__all__ = ["CommandError", "Sandbox"]
