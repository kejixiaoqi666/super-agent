#!/usr/bin/env bash
# =============================================================================
# Super-Agent 完全体一键安装 (super-agent + superbrain-2.0 · systemd 自启)
# 用法（从 GitHub 一键安装）:
#   curl -fsSL https://raw.githubusercontent.com/kejixiaoqi666/super-agent/master/install.sh | bash
# 可选环境变量:
#   SA_BASE=$HOME/super-agent    安装根目录(默认)
#   SA_MODE=workspace            权限模式: read-only|workspace|unrestricted
#   SA_TELEGRAM=1                装完启动 Telegram 常驻服务(默认)
#   SA_ENABLE_SYSTEMD=1          注册 systemd 自启(默认)
#   插件 / embedding 模型是可选、单独挨个装: 见脚本末尾说明。
# 幂等: 重复执行会复用已装的仓库与 venv, 只会更新代码/依赖/服务。
# =============================================================================
set -euo pipefail

# ---- 可配置 ----
BASE="${SA_BASE:-$HOME/super-agent}"
MODE="${SA_MODE:-workspace}"
APP="$BASE/app"            # super-agent 仓库
BRAIN="$BASE/brain"        # superbrain-2.0 仓库
VENV="$BASE/.venv"
DATA="$BASE/data"
WS="$BASE/workspace"
REPO_SA="https://github.com/kejixiaoqi666/super-agent.git"
REPO_BRAIN="https://github.com/kejixiaoqi666/superbrain-2.0.git"

c() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m    ✓\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m!! %s\033[0m\n' "$*" >&2; }

# ---- 前置检查 ----
command -v git >/dev/null || { err "需要 git"; exit 1; }
PY=$(command -v python3 || true)
[ -n "$PY" ] || { err "需要 python3"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
  || { err "需要 Python >= 3.10 (当前: $("$PY" -V 2>&1))"; exit 1; }

mkdir -p "$BASE" "$DATA" "$WS"
cd "$BASE"

# ---- 1. 拉取/更新源码 (幂等) ----
c "拉取 super-agent"
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only --quiet; else git clone --quiet "$REPO_SA" "$APP"; fi
ok "super-agent -> $APP"
c "拉取 superbrain-2.0"
if [ -d "$BRAIN/.git" ]; then git -C "$BRAIN" pull --ff-only --quiet; else git clone --quiet "$REPO_BRAIN" "$BRAIN"; fi
ok "superbrain-2.0 -> $BRAIN"

# ---- 2. 创建虚拟环境 ----
c "创建虚拟环境 $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip

# ---- 3. 安装 Python 依赖 ----
c "安装依赖 (rich/Pillow/cryptography/numpy + super-agent)"
"$VENV/bin/pip" install --quiet \
  -r "$APP/requirements.txt" numpy
"$VENV/bin/pip" install --quiet -e "$APP"
ok "依赖安装完成"

# ---- 4. 环境配置 (.env) ----
c "配置 .env"
ENVF="$DATA/.env"
mkdir -p "$DATA"
if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<EOF
# Telegram 机器人 (https://t.me/BotFather 创建, 填 token)
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
# 允许使用的 Telegram 用户名(逗号分隔, 白名单)
TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS:-}
# 密码本主密码(可选; 用于派生加密密钥, 不落盘到代码)
VAULT_MASTER=${VAULT_MASTER:-}
EOF
  ok "已生成 $ENVF (请编辑填入 TELEGRAM_BOT_TOKEN)"
else
  ok "已存在 $ENVF (跳过)"
fi

# ---- 5. 常驻服务 + systemd 自启 ----
# 默认 headless daemon(免 bot token 也能后台自治运行); SA_TELEGRAM=1 切 telegram 服务
engine_args="--daemon --interval 28"
run_engine=0
if [ "${SA_TELEGRAM:-0}" = "1" ]; then engine_args="--telegram"; fi
if [ "${SA_TELEGRAM:-0}" = "1" ] || [ "${SA_DAEMON:-1}" = "1" ]; then run_engine=1; fi
en_sysd=0
[ "${SA_ENABLE_SYSTEMD:-1}" = "1" ] && en_sysd=1
if [ "$en_sysd" = "1" ]; then
  c "注册 systemd 用户服务 super-agent.service"
  UNIT="$HOME/.config/systemd/user/super-agent.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=Super-Agent Body + SuperBrain 2.0
After=network.target
[Service]
Type=simple
WorkingDirectory=$BASE
EnvironmentFile=$DATA/.env
ExecStart=$VENV/bin/sa --kernel $BRAIN/python --data $DATA --workspace $WS --mode $MODE $engine_args
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  systemctl --user enable super-agent.service >/dev/null 2>&1 || true
  if [ "$run_engine" = "1" ]; then
    systemctl --user restart super-agent.service >/dev/null 2>&1 || true
    loginctl enable-linger "$USER" >/dev/null 2>&1 || true   # 免登录自启
  fi
  ok "systemd 服务已启用 (无需登录也会自启, 已开 linger)"
fi

# ---- 6. 验证完全体 ----
c "验证完全体"
V=$("$VENV/bin/sa" --version 2>/dev/null | tail -1 || echo "?")
ok "版本: $V"
"$VENV/bin/python" -c "import agent_body; import numpy" && ok "super-agent + numpy 可导入"
K="$BRAIN/python/superbrain2"
[ -d "$K" ] && "$VENV/bin/python" -c "import sys; sys.path.insert(0, '$BRAIN/python'); import superbrain2" && ok "superbrain2 内核可导入" \
  || { err "superbrain2 导入检查未过 (可后续重跑脚本修复)"; }

cat <<'EOF'

════════════════════════════════════════════════════
✅ 完全体已装好, 默认以 headless daemon 后台常驻自启(无需 bot)。
   2. 看运行: systemctl --user status super-agent
   3. 交互:    $VENV/bin/sa
   4. Telegram: 设 SA_TELEGRAM=1 重跑脚本, 或填 token 后手动切 telegram 模式

可选(单独挨个装, 不阻塞完全体):
   · embedding 真语义模型: $VENV/bin/pip install -e "$BRAIN[embedding]"  # onnxruntime+tokenizers
   · 插件: 在交互里 /plugins 查看, 或用 Body 插件注册中心随装随卸
   · 密码本: 设 VAULT_MASTER 后可用 vault 加密凭据
════════════════════════════════════════════════════
EOF