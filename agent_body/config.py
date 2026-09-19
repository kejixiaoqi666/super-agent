"""配置层 —— 交互式管理 agent 的对接信息（API / 机器人 / 授权）。

原则：
  - **不硬编码**：API key、bot token、授权信息绝不写进代码或被 git 跟踪的文件。
  - 存在 `.env`（已在 .gitignore），0600 权限。
  - 提供交互式 `configure()`：逐项提示填写，好看简单。
  - 提供 `load()`：读取 .env + 环境变量（env 优先）。
"""
from __future__ import annotations

import os
from pathlib import Path

# .env 路径：优先项目根，其次 super-agent 包同层
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
ENV_TEMPLATE = Path(__file__).resolve().parent / "env.example"

# 可配置项：key -> (说明, 是否敏感[回显时打码])
_CONFIG_KEYS = [
    ("TELEGRAM_BOT_TOKEN", "Telegram 机器人 token（@BotFather 获取）", True),
    ("TELEGRAM_ALLOWED_USERS", "允许的用户数字 ID（逗号分隔）", False),
    ("SUPERBRAIN_LLM_BASE", "LLM 接口地址（base url，如 https://xxx/v1）", False),
    ("SUPERBRAIN_LLM_MODEL", "LLM 模型名（如 deepseek-v4-flash）", False),
    ("SUPERBRAIN_LLM_KEY", "LLM API key（若与 LLM_API_KEY 不同）", True),
]


def load(env_path: str | Path | None = None) -> dict:
    """读取配置：先 .env 文件，再用已存在的环境变量覆盖。

    返回 dict（不含 token 明文之外的东西，token 可随后用 get_secret 取）。
    """
    path = Path(env_path) if env_path else ENV_PATH
    cfg: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    # 环境变量覆盖 .env
    for k, _, _ in _CONFIG_KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


# ---- 高级功能开关（能力保留·默认收敛）----
# 这些能力都保留、可一键开；默认关，避免默认状态背负复杂度(不困扰/易维护)。
FEATURE_DEFAULTS = {
    "sovereign": False,        # 主权开放(插件/自进化/升级治理)
    "self_evolution": False,   # 自进化思考(观察/提案/自主目标/自主进化)
    "self_ops": False,         # 自运维/自测/自主tick
    "router": False,           # 大模型路由(direct/brain 分流)
    "streaming": False,        # 流式出字
}


def features(env_path: str | Path | None = None) -> dict:
    """读取功能开关。env 里 FEATURE_<名>=1 开启，否则默认关。"""
    cfg = load(env_path)
    out = {}
    for name, default in FEATURE_DEFAULTS.items():
        v = cfg.get(f"FEATURE_{name.upper()}")
        out[name] = (str(v).strip().lower() in ("1", "true", "yes", "on")) \
            if v is not None else default
    return out


def feature_enabled(name: str, env_path: str | Path | None = None) -> bool:
    return features(env_path).get(name, False)


def get_secret(key: str) -> str:
    """取单个配置值（敏感项也从 .env/环境取）。"""
    return load().get(key, "")


def save(values: dict, env_path: str | Path | None = None) -> str:
    """把配置写回 .env（0600）。返回路径。"""
    path = Path(env_path) if env_path else ENV_PATH
    # 保留已有非覆盖项
    merged = load(env_path=path)
    merged.update(values)
    lines = []
    for k, desc, _ in _CONFIG_KEYS:
        if k in merged:
            lines.append(f"# {desc}\n{k}={merged[k]}")
    for k, v in merged.items():
        if k not in {kk for kk, _, _ in _CONFIG_KEYS}:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return str(path)


def _llm_key_ready(cfg) -> bool:
    """LLM key 是否就绪：LLM_API_KEY（环境变量）或 SUPERBRAIN_LLM_KEY（.env）。"""
    return bool(cfg.get("SUPERBRAIN_LLM_KEY")
                or os.environ.get("LLM_API_KEY"))


def has_required() -> bool:
    """bot + 至少一个 LLM 通道已配。"""
    cfg = load()
    return bool(cfg.get("TELEGRAM_BOT_TOKEN") and
                cfg.get("TELEGRAM_ALLOWED_USERS") and
                _llm_key_ready(cfg))


def mask(value: str) -> str:
    """敏感值打码展示。"""
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(len(value) - 8, 3) + value[-4:]


def status_report() -> dict:
    """配置状态摘要（TUI/CLI 展示用，敏感项打码）。"""
    cfg = load()
    out = {}
    for k, desc, sensitive in _CONFIG_KEYS:
        v = cfg.get(k, "")
        out[k] = {"desc": desc, "set": bool(v),
                  "value": mask(v) if sensitive else (v or "(未设置)")}
    out["llm_key_ready"] = _llm_key_ready(cfg)
    return out


# ---------- 交互式配置 ----------
def configure(console=None, env_path: str | Path | None = None) -> dict:
    """交互式逐项填写配置（好看简单）。返回更新后的配置。"""
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.panel import Panel
    c = console or Console()
    c.print(Panel.fit("[bold cyan]⚙️  Super-Agent 配置向导[/bold cyan]\n"
                      "[dim]API / 机器人 / 授权——逐项填写，回车跳过保留原值[/dim]",
                      border_style="cyan"))
    cfg = load(env_path=env_path)
    updates = {}
    for k, desc, sensitive in _CONFIG_KEYS:
        cur = cfg.get(k, "")
        label = desc
        hint = f"（当前: {mask(cur) if sensitive else (cur or '未设置')}）" if cur else ""
        val = Prompt.ask(f"{label}{hint}", default="",
                         password=sensitive)
        if val:
            updates[k] = val.strip()
    if updates:
        p = save(updates, env_path=env_path)
        c.print(f"[green]✓ 已保存配置到 {p}[/green]")
    return load(env_path=env_path)
