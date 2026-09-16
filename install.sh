#!/usr/bin/env bash
# Super-Agent 一键安装（Linux / macOS）
# 用法: curl -fsSL https://github.com/kejixiaoqi666/super-agent/releases/latest/download/install.sh | bash
set -euo pipefail

REPO="kejixiaoqi666/super-agent"
VERSION="${VERSION:-latest}"
PIP="${PIP:-python3 -m pip}"

echo "==> Super-Agent 安装向导"
echo "    版本: ${VERSION}"

# 0) 检测 OS/arch
OS="$(uname -s)"
ARCH="$(uname -m)"
echo "==> 检测到: ${OS} / ${ARCH}"

# 1) 检测 python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! 需要 python3 (>=3.10)，未找到。请先安装 Python，再重跑本脚本。" >&2
  exit 1
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "==> python3: ${PYVER}"

# 2) 建独立 venv（避免污染系统 Python）
VENV_DIR="${SUPER_AGENT_VENV:-$HOME/.super-agent/venv}"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "==> 创建虚拟环境: ${VENV_DIR}"
  python3 -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# 3) 安装 super-agent（从 GitHub Release 拉最新 wheel，回退到 PyPI）
echo "==> 安装 super-agent ..."
if [ "$VERSION" = "latest" ]; then
  "$VENV_PIP" install --upgrade "super-agent" || \
    "$VENV_PIP" install --upgrade "git+https://github.com/${REPO}.git"
else
  "$VENV_PIP" install --upgrade "super-agent==${VERSION}" || \
    "$VENV_PIP" install --upgrade "git+https://github.com/${REPO}.git@${VERSION}"
fi

# 4) 生成 .env（如缺）
ENV_FILE="$HOME/.super-agent/.env"
mkdir -p "$HOME/.super-agent"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> 生成 .env: ${ENV_FILE}（请编辑填入你的 API / Bot Token）"
  cat > "$ENV_FILE" <<'EOF'
# Super-Agent 环境变量（0600 权限）
# LLM API key（大脑内核用）
LLM_API_KEY=
# Telegram Bot Token（可选，启用 TG 轮询）
TELEGRAM_BOT_TOKEN=
EOF
  chmod 600 "$ENV_FILE"
fi

# 5) 提示完成
echo
echo "==> 安装完成！"
echo "    CLI 入口: $VENV_DIR/bin/sa"
echo "    或激活环境后使用: sa"
echo "    配置文件: ${ENV_FILE}"
echo
echo "建议把下面这行加进 ~/.bashrc 方便使用:"
echo "  export PATH=\"\$HOME/.super-agent/venv/bin:\$PATH\""
