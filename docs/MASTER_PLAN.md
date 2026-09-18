# Super-Agent 总纲（MASTER PLAN）—— 整体整合版

> 本文件是项目的**最高总纲**：把所有想法整合成一份结构化蓝图。
> 愿景 / 哲学 / 架构 / 已建成 / 待建 / 分阶段 / 验证纪律 / 仓库布局，一页看全。
> ROADMAP.md 是各模块的详细技术方案，本文件是总体规划入口。

---

## 1. 一句话愿景

**LLM 是发动机已经很强，我们做的是那个"载体"——一套像操作系统一样调度资源、不会越用越坏的智能体运行系统。**

核心定位：给自然语言模型一个**特别好的载体**，而不是又一个功能堆砌的框架。

---

## 2. 三大哲学（贯穿一切的判断准则）

| 哲学 | 内容 | 反例（市面通病） |
|---|---|---|
| **内核化分离** | 大脑=独立内核 + 身体=可替换载体，**分开更新**，身体不 import 内核内部，走契约（BrainPort） | 大脑和身体耦合，更新联动爆炸 |
| **资源调度器** | 上下文/记忆/工具/任务/图片，全部"预算→分级→调度→回收" | 只增不减，越用越乱越笨 |
| **自管安全** | 安全防误伤、不妨碍自己人；密码本加密但可解密查看（非银行/支付级） | 死板门禁，连重启/看密码都拦 |

---

## 3. 架构总览（语言策略已定稿）

```
┌─────────────────────────────────────────────────────────┐
│                    客户端层（Rust）                      │
│     Windows / Linux / macOS 同一套，本地压缩/执行       │
├─────────────────┬─────────────────┬─────────────────────┤
│ 身体 super-agent│ 大脑 superbrain │ 系统脚本           │
│ （Python载体）  │ 2.0（Rust内核） │ sh / ps1 / zsh      │
│  编排/工具/TUI  │  记忆/检索/状态 │  安装器/系统操作    │
├────────────────────┴────────────────────────────────────┤
│         资产区 / 账号区 / 存储区 / 上下文+token         │
└─────────────────────────────────────────────────────────┘
```

**语言分工（你的测试结论定稿）：**
- **Rust**：客户端 + 大脑内核（稳定+高性能）
- **Python**：身体编排/工具生态/TUI（生态最全）
- **原生脚本**：系统级操作与安装器

---

## 4. 已建成（已验证，别丢）

| 模块 | 位置 | 状态 |
|---|---|---|
| 大脑孔位层 BrainPort（契约，不用 import 内核内部） | agent_body/kernel/ | ✅ 25 测试 |
| AgentLoop 自主执行（状态机/验证/失败分类） | agent_body/loop/ | ✅ 44 测试 |
| 配置层（.env 交互式，不硬编码，0600） | agent_body/config.py | ✅ |
| token 计费 + 上下文统计 | agent_body/stats.py | ✅ |
| TUI 多表面（对话/任务/设置/用量） | agent_body/tui.py | ✅ 真机验证 |
| Rust 内核（记忆/检索/状态） | superbrain-2.0 | ✅ 独立仓库 |
| 存储规划（五区布局+保留时长回收） | agent_body/storage.py | ✅ |
| 资产文件夹（skills/MCP/截图分类） | agent_body/assets.py | ✅ |
| 图片系统（统一存放+PNG压缩入库） | agent_body/images.py | ✅ |
| 密码本（加密+可解密+分级+防误伤） | agent_body/vault.py | ✅ |
| MCP 协议（客户端+服务器，stdio） | agent_body/mcp/ | ✅ |
| 结构化输出（JSON Schema 校验） | agent_body/struct.py | ✅ |
| 流式输出（TG 打字效果+生成器） | agent_body/stream.py | ✅ |
| 插件系统（发现/加载/生命周期） | agent_body/plugins/ | ✅ |
| 可观测性（结构化 trace+统一日志） | agent_body/observe.py | ✅ |
| 断点续跑（未完成清单+/resume） | agent_body/resume.py | ✅ |
| 重试预算（attempts+wait 双上限） | agent_body/retry.py | ✅ |
| 技能系统（skills/**/SKILL.md 发现/加载/注入） | agent_body/skills.py | ✅ 2026-09 |
| 真实 Provider 路由（OpenAI兼容HTTP + 失败切换） | memory_plane/model_router.py | ✅ 2026-09 |
| 子代理/委派（隔离有界 + 并行） | agent_body/delegation.py | ✅ 2026-09 |
| 会话全文检索（FTS5 trigram 中文子串） | agent_body/session_store.py | ✅ 2026-09 |
| 定时任务（@every/五段cron/一次性） | agent_body/scheduler.py | ✅ 2026-09 |
| 网关鉴权（principal白名单 + 哈希API-key门禁） | agent_body/auth.py | ✅ 2026-09 |
| 经验自动固化（任务成功N次→自动生成SKILL.md） | agent_body/skill_compiler.py | ✅ 2026-09 |
| 跨会话自我重建（/new后新会话首轮自动恢复上下文） | agent_body/handover.py | ✅ 2026-09 |
| **预动性**（任务完成后主动预测+只读预检用户下一步, /next + 数据驱动习惯学习 /learn） | agent_body/proactive.py | ✅ 2026-09 |
| 委派资源治理（并行cap+步预算+批次上限, DelegationGovernor） | agent_body/delegation.py | ✅ 2026-09 |
| 会话权限隔离（委派子代理默认只读, 写入/执行落session沙箱） | agent_body/delegation.py | ✅ 2026-09 |
| 自动续接（当轮真实输入占窗口比例→精确锚点写向量记忆→后继会话无缝衔接） | agent_body/continuity.py | ✅ 2026-09 |
| 每轮真实输入记账（last/max_input, 区分累计成本vs本轮思考量, over_compressed触发） | agent_body/budget.py | ✅ 2026-09 |
| 执行 daemon（有界worker池+内存队列+多运行时shell/python/node+错误分类+内存上限+沙箱清理, 高并发省资源） | agent_body/exec/daemon.py | ✅ 2026-09 |
| Body 批量执行 run_scripts（懒加载executor, 保序并发, close回收） | agent_body/runtime.py | ✅ 2026-09 |
| Autopilot 自动操作（Driver抽象 describe/act/文本定位 + Browser后端 Playwright/CDP挖DOM, 惰性加载; 真实浏览器E2E待装playwright） | agent_body/autopilot/ | ✅抽象+后端 2026-09 |
| 主权开放 阶段①（开放插件注册中心: 随装随卸即净/坏插件隔离不影响内核/动态加载执行 + 内核只读门: kernel区写被拒提示走升级队列, plugin/free放行） | agent_body/sovereign/ | ✅ 2026-09 |
| 主权开放 阶段②（自我修改限定插件层: SelfMod 增/改/卸插件+写受管文件, 全留痕trail, restore_last_good回滚, 内核写经gate拒绝带升级提示） | sovereign/self_mod.py | ✅ 2026-09 |
| 主权开放 阶段③（自进化思考: evolution/ observer观察→proposer思考提案→ledger状态机(未approved不得apply) →approval批准闸门; Body.run_scripts失败自动入error观察带诊断） | agent_body/evolution/ | ✅ 2026-09 |
| 主权开放 阶段④（升级治理: upgrade_queue 需求文档存档→排队→门禁approve→测试→并入merge, 不可跳过, 存底代码文档） | sovereign/upgrade_queue.py | ✅ 2026-09 |
| 主权开放 阶段⑥（信任模式: TrustMode guided/sovereign 切换, 持久化; 只改AI自主体验, 内核只读+底层批准硬约束不随mode变） | sovereign/trust.py | ✅ 2026-09 |
| 主权开放 阶段⑤（层面一开放: 完整上下文/全量工具/思考进化循环接 AI 自主） | — | ⏳ |

**总测试数：384 全绿。** 这些是地基，后期规划全部建立在它们之上。

---

## 5. 待建总览（按模块）

| 模块 | 内容 | 详见 |
|---|---|---|
| **稳定性** | 自查模块 / 连贯任务队列 / 熔断器 / 重试预算 / 超时 / 未完成清单 / 进度条 / 命令沙箱回收 | ROADMAP §3 |
| **上下文治理** | token 预算压缩 / 工具惰性加载 / **项目感知精挑输入** / 记忆分层打通 | ROADMAP §8 |
| **客户端发布** | Rust 客户端 / 一键安装 / GitHub Actions 多平台 | ROADMAP §5 |
| **空间与资产** | 存储规划 / 资产文件夹（skills/MCP/截图侧挂载） | ROADMAP §6 |
| **图片系统** | 统一存放 / 搬 Squoosh 编码器 / PNG 入库 / 80% 压缩上传 / 保留时长 | ROADMAP §7 |
| **密码本** | 加密密码本 / 可解密查看 / 凭据分级 / 防误伤保护 | ROADMAP §4 |

---

## 6. 分阶段路线

- **Phase 1 稳定性**：自查 + 连贯队列 + 熔断/重试/超时 + 未完成清单 + 进度条 + 沙箱回收
- **Phase 2 上下文治理**：预算压缩 + 工具惰性加载 + 项目感知精挑 + 记忆分层
- **Phase 3 发布**：Rust 客户端 + 一键安装 + 打包 + Actions
- **Phase 4 资产与存储**：存储规划 + 资产文件夹 + 图片压缩（Squoosh/PNG/80%）
- **Phase 5 密码本**：加密密码本 + 可解密 + 防误伤

---

## 7. 验证纪律（贯穿一切，不堆营销词）

- 自查 → 用坏服务器测
- 进度条 → 用长任务测
- 图片压缩 → 用真实截图测体积
- 安装脚本 → 在干净环境测
- 每个模块：单元测试 + 真机验证 + 独立回读外部状态（不靠自报）

---

## 8. 仓库布局（分开更新）

| 仓库 | 角色 | 更新节奏 |
|---|---|---|
| `superbrain-2.0` | 大脑内核（Rust） | 独立 |
| `super-agent` | 身体+客户端（Python/Rust） | 独立 |

**升级原则**：大脑加功能，身体不动（契约不变）；身体加表面，大脑不动。各自仓库，各自 CI/测试。