# Super-Agent（SPA）

> SuperBrain 2.0 的身体层：让大脑能够对话、调用工具、接入 Bot 并持续运行。

[身体层说明](BODY.md) · [大脑孔位层](KERNEL.md) · [总体规划](docs/MASTER_PLAN.md) · [详细技术方案](docs/ROADMAP.md) · [SuperBrain 2.0 内核](https://github.com/kejixiaoqi666/superbrain-2.0)

## 当前状态

SPA 目前包含 AgentWorkbench 控制面和 `agent_body` 运行时。它已能接入 SuperBrain 2.0 的 Python 内核，提供 CLI、Telegram 私聊轮询、工作目录文件工具、Shell 工具、会话隔离、状态持久化和可配置执行模式。

---

## 已落地能力（Phase 1-5）

| 阶段 | 能力 | 模块 |
|---|---|---|
| P1 稳定性 | 死循环防护 / 自查模块 / 连贯任务队列 / 熔断重试 / 沙箱 | `loop/` `safety/` |
| P2 上下文治理 | 项目感知精挑输入 / 工具惰性注入 / 记忆分层 recall / token 预算 | `context.py` `curate.py` `budget.py` |
| P3 客户端 | Rust 跨平台启动器（对接身体层 `sa`） | `client/` |
| P4 资产与存储 | 五区存储布局 / 资产分类挂载 / 图片统一存放+PNG压缩入库 / 保留时长回收 | `storage.py` `assets.py` `images.py` |
| P5 密码本 | 加密存储 / 主密码派生(不落盘) / 凭据分级 / 支付级防误伤 | `vault.py` |
| 成熟补齐 | MCP 协议(客户端+服务器) / 结构化输出校验 / 流式输出(TG打字) / CI测试门禁 | `mcp/` `struct.py` `stream.py` `.github/workflows/test.yml` |
| P1/P2 补齐 | 插件系统 / 可观测性(trace) / 断点续跑(/resume) / 重试预算 | `plugins/` `observe.py` `resume.py` `retry.py` |
| 成熟补齐2 | **技能系统**(发现/加载/注入SKILL.md) / **真实Provider路由**(失败切换) / **子代理委派**(并行) / **会话FTS检索**(中文) / **Cron定时** / **网关鉴权**(白名单+哈希key) | `skills.py` `model_router.py` `delegation.py` `session_store.py` `scheduler.py` `auth.py` |

**常用 CLI 命令**（`python -m agent_body` 或 Rust `sa` 后）：

```
/storage    查看五区存储占用
/gc         回收超保留时长的过期资产(资产区+缓存区)
/image <路径> [session]   图片压缩入库(默认转PNG)
/vault      密码本操作(需 --vault-master 启动)
/pending    未完成清单(FAILED/CANCELED 可续跑任务)
/resume [id]   断点续跑(全部或指定任务)
/plugins    发现并列出插件
/trace [id]  查看结构化执行轨迹
/skills       列出已发现技能(skills/**/SKILL.md)
/skills-load <名...>   渲染技能指令块注入上下文
/cron-add <id> <规格(@every 30m|五段cron|@ISO)> [提示词]
/cron-list    列出定时任务
/cron-rm <id> 删除定时任务
/cron-run     立即执行所有到期任务(默认跑 payload.prompt 经大脑)
/search <关键词>   会话全文检索(对话自动记入 transcript.db)
/delegate <目标>   委派一个子代理任务
```

> 对话自动写入 `transcript.db`(FTS5),Cron 到期默认执行其 `prompt` 并经大脑
> 生成结果(存 `cron_output`)。委派用 `SA_PROVIDER_*` / `OPENAI_*` 环境配置的真实
> provider,无则回退离线 Echo。

---

## 发布与安装

打 `v*` 标签即自动触发 [GitHub Actions 发布工作流](.github/workflows/release.yml)，构建多平台 Python wheel，创建 GitHub Release 并附带一键安装脚本。

**一键安装**（Linux / macOS）:

```bash
curl -fsSL https://github.com/kejixiaoqi666/super-agent/releases/latest/download/install.sh | bash
```

**一键安装**（Windows PowerShell）:

```powershell
irm https://github.com/kejixiaoqi666/super-agent/releases/latest/download/install.ps1 | iex
```

**手动发布**：打标签即可

```bash
git tag v0.3.0 && git push origin v0.3.0
```

**pip 直装**:

```bash
pip install super-agent      # 或指定版本 pip install super-agent==0.3.0
```

安装后 `sa` 命令可用（`sa --help` 看选项；`sa --telegram` 启用 TG 轮询）。大脑内核 `superbrain-2.0` 为独立仓库，按 [BODY.md](BODY.md) 说明接入。

---

## 新增：SuperBrain 2.0 身体层

`agent_body/` 已接入 SuperBrain 2.0 的真实内核，提供 CLI、Telegram 私聊轮询、文件/Shell 工具、执行模式与会话状态持久化。使用方法和当前验证边界见 [BODY.md](BODY.md)。下文描述原有独立 Memory Plane；两者尚未自动同步。

本目录是对话式 Agent 的本地记忆/控制面原型。它提供 SQLite/WAL、分层作用域、候选→接受生命周期、基于成功反馈的可解释召回、UTF-8 上下文预算、结构化 checkpoint、认证 loopback API、请求级连接、Channel/Principal 契约、Auth Profile 元数据、Plugin Manifest、ModelRouter、Capability Lease 和 Agentd 任务状态机；真实 Telegram、LLM、SSH、OAuth 和凭据值仍由后续 Adapter 接入。

Hermes 兼容层按本机 Hermes 的目录约定发现 `skills/**/SKILL.md` 与 `memory.md`/`user.md`，输出元数据和 SHA-256。它不导入令牌、不执行技能、不加载 Hermes Python 进程，后续可在显式批准后增加受控导入。

## 运行测试

```powershell
& "C:\Users\AUAS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s tests -v
```

## 启动本地 API

```powershell
& "C:\Users\AUAS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m memory_plane --config config.toml
```

API 默认仅绑定 `127.0.0.1`，MVP 使用请求级 SQLite 连接并以 `max_workers` 有界排队。真实客户端/Telegram Gateway 必须在其自身完成身份认证，再通过 IPC 或受保护的本地网关调用；本原型没有把“请求里的 owner”当作认证依据。生产环境仍建议将认证后的 Gateway 与 Memory API 之间改为命名管道/Unix socket 或 mTLS。

`memory_plane.client.MemoryClient` 是后续 Channel Adapter 的最小调用面，凭据只从进程环境读取并通过 loopback Bearer 发送；它不会接受或回显 memory API 返回的 Secret 值。

核心规则：

- 新记忆先是 `candidate`，接受后才参与召回；
- 每个 owner/project 有作用域过滤；
- 同一 project 的同一 `fact_key` 不允许静默双活；
- 反馈必须绑定 `task_id + evidence_ref`，重复任务反馈幂等；
- 排序前先过滤过期、被覆盖和无权限内容；
- P0 约束超出预算直接失败，不被静默截断；
- `forget` 是数据库逻辑擦除，外部备份清理需要单独的保留策略；
- 凭据、Token、私钥和密码不得写入 Memory Plane，只能引用 Auth/Vault。

控制面契约：

- `channels.py`：Channel Adapter、loopback channel 和 Telegram identity 映射契约；
- `auth.py`：官方/第三方 Provider 的 metadata-only Auth Profile，Token 只能引用 Vault/env/keychain；
- `plugins.py`：版本、API、能力、hash 的 manifest registry，不在核心进程加载第三方代码；
- `model_router.py`：Provider-neutral ModelRouter，当前提供 EchoProvider 作为离线测试替身；
- `agentd.py`：任务状态迁移、Capability Lease、执行器注入和幂等收据。

当前 `agentd` 和 EchoProvider 是本地契约实现，不代表已连接真实服务或具有生产级权限。生产接入必须补上 IPC 身份认证、真实 Secret Broker、Telegram OAuth/API、SSH Executor、持久任务表和独立 supervisor。

Hermes 的实时 Gateway IPC、SessionDB/FTS5 会话全文导入、真实 OAuth/API 登录和 Provider 调用仍未接入；当前适配层保持只读，以免污染现有 Hermes 安装。


