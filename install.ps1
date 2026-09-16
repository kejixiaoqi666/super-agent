# Super-Agent 一键安装（Windows）
# 用法（PowerShell）: irm https://github.com/kejixiaoqi666/super-agent/releases/latest/download/install.ps1 | iex
$ErrorActionPreference = "Stop"

$Repo = "kejixiaoqi666/super-agent"
$VenvDir = Join-Path $HOME ".super-agent\venv"
$EnvFile = Join-Path $HOME ".super-agent\.env"

Write-Host "==> Super-Agent 安装向导"

# 1) 检测 python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Host "!! 未找到 python，请先安装 Python 3.10+，再重跑本脚本。" -ForegroundColor Red
  exit 1
}
Write-Host "==> python: $($py.Source)"

# 2) 建独立 venv
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
  Write-Host "==> 创建虚拟环境: $VenvDir"
  python -m venv $VenvDir
}
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

# 3) 安装 super-agent
Write-Host "==> 安装 super-agent ..."
& $VenvPip install --upgrade "super-agent"
if ($LASTEXITCODE -ne 0) {
  & $VenvPip install --upgrade "git+https://github.com/$Repo.git"
}

# 4) 生成 .env（如缺）
if (-not (Test-Path $EnvFile)) {
  New-Item -ItemType Directory -Force -Path (Split-Path $EnvFile) | Out-Null
  Write-Host "==> 生成 .env: $EnvFile（请编辑填入你的 API / Bot Token）"
  @"
# Super-Agent 环境变量
# LLM API key（大脑内核用）
LLM_API_KEY=
# Telegram Bot Token（可选）
TELEGRAM_BOT_TOKEN=
"@ | Set-Content -Path $EnvFile -Encoding UTF8
}

Write-Host ""
Write-Host "==> 安装完成！" -ForegroundColor Green
Write-Host "    CLI 入口: $(Join-Path $VenvDir 'Scripts\sa.exe')"
Write-Host "    配置文件: $EnvFile"
