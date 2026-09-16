# AgentWorkbench 身体层

认知内核使用 https://github.com/kejixiaoqi666/superbrain-2.0 ，本轮核对提交为 `65876d51e6a199b16b1bf04d5c5ba1ee582c0cf0`，Python 包版本 `0.2.0`。本地克隆位于同级 `superbrain-2.0/`。未改动上游内核。

## 已打通

- **大脑孔位层（kernel/）**：agent 通过稳定契约 `BrainPort` 调用大脑，不直接 import 内核内部；
  registry 插件机制支持换内核而 agent 主体零改动，契约版本感知，BrainPort 是 ABC 拦截不完整实现。
  详见 [KERNEL.md](KERNEL.md)。孔位层专项测试全绿。
- SuperBrain 的真实聊天循环调用身体工具，接收结果后继续回复。
- 文件读写、Shell 执行、CLI 对话和 Telegram 私聊轮询入口。
- 每个 session 使用 SHA-256 命名的独立 SQLite；重启恢复认知、人格和会话状态。
- `/state` 查看状态，`/tick` 手动推进自主思考；没有后台自动发言。
- `read-only` 禁止写入和执行；`workspace` 允许工作目录内文件读写；`unrestricted` 允许文件访问与 Shell，无逐操作批准。策略由启动者设置，模型不能修改。

内核负责情绪、人格、关系、记忆、目标与模型对话；身体负责入口、实际工具与运行生命周期。原有 `memory_plane/` 仍保留，暂未自动同步到大脑，避免重复记忆或互相覆盖。

## 启动

在 AgentWorkbench 目录运行，Python 3.10+。默认从同级内核仓库的 `python/` 导入，不要求先编译 Rust：

```bash
python -m agent_body --workspace ./work --data ./.body-data --mode workspace
python -m agent_body --workspace ./work --mode unrestricted
```

真实模型通过内核的 `SUPERBRAIN_LLM_BASE`、`SUPERBRAIN_LLM_KEY`、`SUPERBRAIN_LLM_MODEL` 环境变量配置。`--kernel /path/to/superbrain-2.0/python` 可指定内核位置。

Telegram 入口需要设置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USERS`（逗号分隔的用户数字 ID）：

```bash
python -m agent_body --telegram --workspace ./work --mode workspace
```

只接收白名单用户私聊，群聊忽略。Telegram offset 在执行前持久化，崩溃后不自动重放可能产生副作用的操作；代价是中断的一轮可能无回复，需要用户核对后重新请求。当前不是可靠任务队列或 exactly-once 系统。

## 验证与边界

本轮自动测试使用真实内核加可控模型替身：验证工具往返、真实文件写入、权限模式、目录越界、不同会话数据库隔离、重启恢复、tick 和 Shell 返回码；Telegram 用模拟 HTTP 验证收件人过滤与 offset。

- 未配置真实模型或 Telegram Token，尚未做外网对话验收。
- 未编译 Rust、未验证加速性能；当前可用 Python 回退。
- 单线程串行运行，SQLite 连接不跨线程。不同会话数据库隔离，但同一身体实例共享工作目录和执行权限；不能当作不可信多租户隔离。
- Shell 限时 30 秒，返回最多 16 KB 输出；尚无跨平台进程树监督，不能承诺结束所有子进程。
- 未实现自重启 supervisor、SSH/Vault、插件执行、桌面 UI、持久任务续跑或自动心跳。这些仍属于身体层后续工作。
- 尚无独立审查；本轮为实现者自检和回归测试。

## 下一步

优先把 Telegram 接入真实模型验收，然后接持久任务队列与独立 supervisor，支持重启后恢复；再将 Skills/Plugin 注册表接入工具目录，增加 SSH 和独立凭据存储。
