"""密码本 Vault —— 加密存储 + 可解密查看 + 凭据分级 + 防误伤保护。

ROADMAP §4 落地：
  - 密码自管：加密存储，可解密查看明文（非银行/支付密码也能看）
  - 服务器密码等收录：自己的凭据统一管理，按重要性分级
  - 防误伤：仅保留防误伤提醒，不拦截自己人（支付/银行级多一道确认，普通级直接给）

技术：
  - 加密：cryptography.Fernet（AES128-CBC+HMAC）。主密码经 PBKDF2HMAC-SHA256
    派生密钥，密钥不落盘（仅存 salt），密文落盘在 config/vault.json（0600）。
  - 分级：normal（普通）/ high（重要：服务器/API）/ payment（支付/银行）。
    payment 级解密需 explicit=True，防止手滑泄露支付类敏感信息。
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False
    InvalidToken = Exception  # cryptography 缺失时兜底，避免 NameError

_TIERS = ("normal", "high", "payment")

# 防误伤确认：payment 级解密默认需要 explicit=True
_PAYMENT_HINT = "这是支付/银行级凭据，解密将暴露明文。确认无误再显式解密。"


class Vault:
    """加密密码本。"""

    def __init__(self, data_dir: str | Path, master_password: str,
                 create_if_missing: bool = True):
        self.path = Path(data_dir) / "config" / "vault.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._master = master_password
        self._fernet = None
        self._salt = b""
        self._verify_token = None
        self._entries: Dict[str, dict] = {}
        if not self.path.exists():
            if not create_if_missing:
                raise FileNotFoundError(f"vault not found: {self.path}")
            self._init_new()
        self._load()
        # 打开后校验主密码：错误密码立即抛错，禁止后续静默写入污染数据
        self._verify_master_password()

    # ---- 初始化 / 密钥 ----
    def _init_new(self) -> None:
        self._salt = os.urandom(16)
        self._entries = {}
        # 校验令牌：用派生密钥加密一个已知字符串，打开时解密它来验证主密码
        self._verify_token = None
        self._save()

    def _derive_key(self) -> bytes:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=self._salt, iterations=200_000)
        return base64.urlsafe_b64encode(kdf.derive(self._master.encode()))

    def _make_verify_token(self) -> str:
        """用当前密钥加密校验令牌（存盘供主密码校验）。"""
        token = self._fernet.encrypt(b"super-agent-vault-ok")
        return token.decode()

    def _fernet_ok(self) -> bool:
        if self._fernet is None:
            if not _HAS_CRYPTO:
                return False
            self._fernet = Fernet(self._derive_key())
        return True

    # ---- 持久化 ----
    def _save(self) -> None:
        if not self._fernet_ok():
            raise RuntimeError("cryptography 库缺失，无法加密存储凭据")
        payload = json.dumps({
            "salt": base64.b64encode(self._salt).decode(),
            "verify": self._make_verify_token(),  # 主密码校验令牌
            "entries": self._entries,  # 值已是 fernet token 密文
        })
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def _load(self) -> None:
        if not self._fernet_ok():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._salt = base64.b64decode(data.get("salt", ""))
            # 重新派生密钥（salt 在 load 后才知道）
            self._fernet = Fernet(self._derive_key())
            self._verify_token = data.get("verify")
            self._entries = data.get("entries", {})
        except Exception:
            # 文件损坏 → 空；主密码校验在 _verify_master_password 统一处理
            self._entries = {}

    def _verify_master_password(self) -> None:
        """打开后校验主密码。错误密码立即抛错，防止后续静默写入污染数据。

        用存储的 verify 令牌解密验证；令牌缺失（旧库）时用最新一条凭据
        试解密兜底。两者都不存在（空库）则假定通过。
        """
        if not _HAS_CRYPTO or (self._verify_token is None and not self._entries):
            return  # 无密码本内容可验（空库），视为通过
        fernet = self._fernet
        if fernet is None:
            raise ValueError("主密码校验不可用（cryptography 缺失）")
        if self._verify_token is not None:
            try:
                if fernet.decrypt(self._verify_token.encode()) != b"super-agent-vault-ok":
                    raise ValueError("主密码错误：无法解密密码本")
                return
            except (InvalidToken, ValueError):
                raise ValueError("主密码错误：无法解密密码本") from None
        # 旧库无 verify 令牌：用最新一条凭据试解密
        try:
            newest = max(self._entries.values(), key=lambda e: e.get("updated_at", 0))
            fernet.decrypt(newest["token"].encode())
        except Exception:
            raise ValueError("主密码错误：无法解密密码本") from None

    # ---- 凭据 CRUD ----
    def set(self, name: str, value: str, tier: str = "normal",
            notes: str = "") -> None:
        """写入/更新一条凭据。value 加密后存储。"""
        if not name or not value:
            raise ValueError("name and value required")
        if tier not in _TIERS:
            raise ValueError(f"tier must be one of {_TIERS}")
        token = self._fernet.encrypt(value.encode())
        self._entries[name] = {
            "token": token.decode(),
            "tier": tier,
            "notes": notes,
            "created_at": self._entries.get(name, {}).get("created_at", time.time()),
            "updated_at": time.time(),
        }
        self._save()

    def get(self, name: str, explicit: bool = False) -> str:
        """解密查看明文。payment 级须 explicit=True（防误伤确认）。"""
        e = self._entries.get(name)
        if e is None:
            raise KeyError(f"no credential named '{name}'")
        if e["tier"] == "payment" and not explicit:
            raise PermissionError(_PAYMENT_HINT)
        token = e["token"].encode()
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken:
            raise ValueError("解密失败：主密码错误或密文被篡改")

    def peek(self, name: str, mask: bool = True) -> Optional[str]:
        """安全预览：默认打码（只露首尾），不触发 payment 确认。"""
        e = self._entries.get(name)
        if e is None:
            return None
        if mask:
            # 不解密也能给打码长度提示（用 token 长度粗估），或返回"已存"
            return f"[{e['tier']}] 已存 (len~{len(e['token']) // 2})"
        return self.get(name)

    def delete(self, name: str) -> bool:
        if name not in self._entries:
            return False
        del self._entries[name]
        self._save()
        return True

    def names(self) -> List[str]:
        return sorted(self._entries)

    def list_meta(self) -> List[dict]:
        """列出所有凭据的元信息（不含明文，安全）。"""
        return [{"name": n, "tier": e["tier"], "notes": e["notes"],
                 "updated_at": e["updated_at"]}
                for n, e in self._entries.items()]

    def count(self) -> int:
        return len(self._entries)
