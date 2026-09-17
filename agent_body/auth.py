"""网关鉴权 GatewayAuth —— principal 白名单 + 哈希化 API-key 门禁。

补齐成熟 agent（Codex/Hermes）的网关身份认证能力。核心设计：
  - principal 白名单：授权哪些用户/通道/调用者；未授权一律拒绝（不再把
    "请求里的 owner" 当认证依据）。
  - API-key 门禁：key 只存 **SHA-256 哈希**（不存明文），比对用
    `hmac.compare_digest`（常数时间，防时序侧信道）。
  - 加载：allowlist 与 key 哈希来自配置/环境；`require_*` 在不满足时抛
    AuthError，绝不静默放行。
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Collection, Optional


class AuthError(PermissionError):
    """鉴权失败（未授权 principal / key 无效）。"""


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_key(plain: str) -> str:
    """对外工具：把明文 key 转成 SHA-256 哈希存库/配库。"""
    return _sha256(plain)


def _is_hex64(v: str) -> bool:
    return len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _hash_keys(values: Collection[str]) -> set:
    """归一化 key 集合：非 64-hex（明文）自动转哈希，杜绝误存明文。"""
    out = set()
    for v in values:
        v = v.strip()
        if v and not _is_hex64(v):
            v = _sha256(v)
        if v:
            out.add(v)
    return out


class GatewayAuth:
    """principal 白名单 + API-key 门禁（key 只存哈希）。"""

    def __init__(self, allowed_principals: Collection[str] = (),
                 api_key_hashes: Collection[str] = (),
                 env_prefix: str = "SA_AUTH_"):
        self.principals = {p for p in allowed_principals if p}
        self.key_hashes = _hash_keys(api_key_hashes)
        self.env_prefix = env_prefix
        self._load_env()

    def _load_env(self) -> None:
        """从环境补充白名单与 key 哈希（逗号分隔；明文自动转哈希）。"""
        for name, target in ((self.env_prefix + "ALLOWED_PRINCIPALS",
                              self.principals),
                             (self.env_prefix + "ALLOWED_API_KEYS",
                              self.key_hashes)):
            val = os.environ.get(name, "")
            if val:
                if target is self.key_hashes:
                    target |= _hash_keys(val.split(","))
                else:
                    for item in val.split(","):
                        if item.strip():
                            target.add(item.strip())

    # ---- principal ----
    def authorize_principal(self, principal: Optional[str]) -> bool:
        """principal 是否在授权白名单内；空/Nones 一律拒绝。"""
        if not principal:
            return False
        return principal in self.principals

    def require_principal(self, principal: Optional[str]) -> None:
        if not self.authorize_principal(principal):
            raise AuthError(f"未授权调用方: {principal!r}")

    # ---- API key ----
    def authorize_api_key(self, key: Optional[str]) -> bool:
        """key 校验：常数时间(hash)比较是否在库。空 key 拒绝。"""
        if not key:
            return False
        h = _sha256(key)
        for stored in self.key_hashes:
            if hmac.compare_digest(h, stored):
                return True
        return False

    def require_api_key(self, key: Optional[str]) -> None:
        if not self.authorize_api_key(key):
            raise AuthError("API key 无效")

    def add_principal(self, principal: str) -> None:
        self.principals.add(principal)

    def add_api_key(self, plain: str) -> None:
        self.key_hashes.add(_sha256(plain))
