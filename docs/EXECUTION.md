# 执行能力 Execution —— 高并发 · 极省资源 · 清洁

> 设计第一原则（用户定调）：**不要臃肿**。内存、硬盘占用越低越好；保持清洁（不留垃圾）；
> 但并发性能要高。游戏自动化暂不做。
>
> 继承现有 `agent_body/exec/sandbox.py` 的好底子：隔离工作目录、**结束自动清理**、
> 输出 100KB 截断、危险命令门禁、重试预算、路径穿越防护。

## 1. 资源纪律（贯穿一切）
| 资源 | 纪律 | 手段 |
|---|---|---|
| 内存 | 空闲 daemon ≤ ~10MB；峰值受控 | worker 池有界 + **async 非线程每任务** + 输出截断(已有) + 惰性加载运行时 |
| 硬盘 | 即用即清，不留沙箱/临时垃圾 | 任务结束回收工作目录(已有)；结果内存态、不落盘除非显式 keep |
| CPU | 并发受 worker 数 × 单任务上限约束 | worker 池 + 超时/资源上限 |
| 清洁 | 惰性加载，不用的运行时/驱动后端不载入 | 运行时按需 spawn；Autopilot 后端按需加载 |

**高并发不靠多线程/多进程堆**，靠**有界 worker 池 + 内存队列 + 异步调度**：
几千个任务是队列里的轻量项，不是几千个进程/线程。

## 2. 架构（精简分层）
```
┌─ 调用方(super-agent 身体/CLI/多agent委派)
│    提交: {runtime, command, timeout, mem_limit, keep?}
├─ Executor daemon（常驻, 单一轻量进程）
│    内存任务队列 → 有界 worker 池(async) → 沙箱执行 → 结构化结果回传
│    错误分类 + 幂等重试 + 清理
├─ Runtimes（多运行时, 惰性按需）
│    shell | python | node | rustbin   ← 任务声明 runtime, 语言无关
└─ Autopilot（自动操作, 内嵌模块, 惰性后端）
     Driver.describe() → 元素/坐标
     Driver.act()      → 点击/输入/滑动/滚动
     后端: browser(CDP) | desktop(AX) | android(UIAutomator)
```

## 3. 执行 daemon（核心交付）
- **队列**：内存环形/优先队列；可选持久化（默认关，省硬盘）
- **worker 池**：默认 4~8（可配）；async 非阻塞；每 worker 拿任务→沙箱跑→回结果
- **沙箱**：复用 `sandbox.py`（自动清理、输出上限、门禁、超时）
- **多运行时**：`{runtime: python}` 用对应解释器执行脚本；python 冷启动慢、node 快——
  海量小脚本倾向 node；抽象统一不绑死一种
- **错误分类**（结构化）：permission / syntax / runtime / timeout / deps / resource / unknown
- **自动修复**：返回结构化错误+日志+退出码，智能体读→改→重跑；幂等重试

## 4. Autopilot（自动操作, 核心回答"不靠截图"）
**原则：能拿布局的绝不截图。** 统一 `Driver` 接口，三个后端惰性加载：
| 后端 | 底层 | 拿布局 |
|---|---|---|
| browser | CDP 挖 DOM | `describe()`→元素+坐标; `act()`→点击/输入 |
| desktop | OS 可访问性树(AX/UIA/AT-SPI)+输入注入 | 控件树+坐标 |
| android | ADB + UIAutomator 控件树 / AccessibilityService | 控件树+坐标 |
拿不到布局的(游戏/纯图形) → 视觉+状态机（**暂不做**, 用户定调）

- 接口：`describe()` 返回当前界面可操作元素（id/text/坐标/类型），`act(action)` 注入
- 内嵌进智能体，多 agent 可并发操作不同目标（各自独立 session/沙箱）

## 5. 跨平台矩阵
| 层 | Linux | macOS | Windows | Android |
|---|---|---|---|---|
| 执行 daemon | ✅ | ✅(Rust/异步) | ✅ | 跑在设备端或通过 ADB 宿主 |
| shell/python/node | ✅ | ✅ | ✅(适配) | termux/受限 |
| browser(CDP) | ✅ | ✅ | ✅ | 可选 |
| desktop(AX) | AT-SPI | AX | UIA | — |
| android(UIAutomator) | — | — | — | ✅(ADB/宿主) |
客户端壳: Android→Kotlin, 桌面→Rust(已有), iOS→Swift。**执行核心语言无关, 壳贴平台原生。**

## 6. 数据模型（极简）
- **Task**: `{id, runtime, command, timeout_s, mem_limit_mb, workdir?, keep?}`
- **Result**: `{id, status: done|error|timeout, code, output(capped), elapsed_ms,
  error_class?, error_msg?, attempts}`
- 结果默认内存态, 过期即弃(设 TTL), 不落盘 → 省硬盘

## 7. 四阶段任务拆解
| 阶段 | 交付 | 验收/资源指标 |
|---|---|---|
| ① 执行 daemon | worker池+队列+多运行时+错误分类+清理 ✅ | 实测: 空闲24MB(含解释器基线), 1000任务4.65s=215/s, 峰值27.5MB, 零残留 |
| ② Autopilot-browser | Driver抽象+调度+CDP后端(点赞/填表/点击) ✅(抽象/后端已交付; 真实浏览器E2E待装playwright) | Driver单测6项过; 浏览器后端代码完整惰性; E2E待装~150MB chromium |
| ③ Autopilot-desktop+android | 桌面 AX 后端 + 安卓 UIAutomator 后端 | 控件树驱动; 跨平台 |
| ④ 多agent并发执行 | 执行 daemon 对接委派治理(复用 governor) | N 任务分发到 worker agents, 预算/隔离/汇总 |

## 8. 验证纪律
- 资源基准：`ps` 实测空闲内存、沙箱跑完 `du` 确认零残留
- 并发基准：1000 个轻任务(echo/计算) → 记录吞吐与峰值内存
- 错误注入：超时/语法错/依赖缺 → 断言返回结构化错误分类 + 日志
- 自动操作：真实浏览器开页 → describe 拿布局 → act 点赞 → 断言成功(非截图)
  [待验证] 需装 playwright chromium(~150MB)。安装命令见 docs/EXECUTION.md。当前 Driver 抽象/后端已单测通过，未在真实浏览器上跑点击。
- 全程真实跑通，不用 mock 冒充
