"""失败分类 FailureClassifier —— 失败后正确决断，而不是盲目重试。

把错误归入五类，每类对应不同处置（对照 Hermes 的错误诊断）：
  permission    → 授权不足，需要用户批准/提供权限
  environment   → 环境/依赖/服务故障，修环境或等恢复
  missing_info  → 信息缺失，需要补充输入
  not_feasible  → 方案本身不可行，换方案（不硬闯）
  param_error   → 参数/输入错误，改参数重试
  unknown       → 无法归类，暂缓并人工介入
"""
from __future__ import annotations

import re
from typing import Dict


_PATTERNS: Dict[str, list] = {
    "permission": [
        r"permission denied", r"forbidden", r"not authorized",
        r"access denied", r"403", r"需批准", r"需要批准", r"insufficient",
    ],
    "environment": [
        r"connection refused", r"connection reset", r"timeout",
        r"no module named", r"command not found", r"module not found",
        r"service unavailable", r"503", r"502", r"network", r"socket",
        r"安装失败", r"连接失败", r"超时",
    ],
    "missing_info": [
        r"requires .* argument", r"missing", r"not provided",
        r"could not resolve", r"no such file", r"filenotfound",
        r"关键信息缺失", r"需要补充",
    ],
    "param_error": [
        r"invalid", r"typeerror", r"valueerror", r"unexpected",
        r"bad request", r"400", r"参数", r"invalid literal",
    ],
    "not_feasible": [
        r"not supported", r"unsupported", r"cannot", r"impossible",
        r"不可行", r"不支持", r"无法实现",
    ],
}


class FailureClassifier:
    """把错误字符串归类为处置类别。"""

    def classify(self, error: str) -> Dict:
        text = str(error).strip()
        if not text:
            return {"category": "unknown", "reason": "empty error",
                    "action": "暂缓并人工介入"}
        for cat, pats in _PATTERNS.items():
            for p in pats:
                if re.search(p, text, re.IGNORECASE):
                    return {"category": cat, "reason": text,
                            "action": _ACTION[cat]}
        return {"category": "unknown", "reason": text,
                "action": _ACTION["unknown"]}


_ACTION = {
    "permission": "需要用户批准或提供权限；不要擅自绕过",
    "environment": "环境/依赖/服务故障；修复环境或稍后重试",
    "missing_info": "信息缺失；向用户索取关键输入",
    "param_error": "参数/输入错误；修正后重试",
    "not_feasible": "方案不可行；换一个可行方案，不要硬闯",
    "unknown": "无法归类；暂缓并请人工介入",
}