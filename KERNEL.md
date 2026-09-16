# 大脑孔位层 KERNEL（Brain Port）

> agent 与大脑 **分离更新**的关键：agent 通过稳定契约 `BrainPort` 调用大脑，
> 不直接 `import` 内核内部 → 大脑升级 / 换内核，agent 主体零改动。

## 架构定位（xray 内核 + 面板）

```
superbrain-2.0/  ← 大脑 = 内核/插件（独立仓库，独立更新）
   │  实现 BrainPort 契约（SuperBrainAdapter 桥）
   ▼
agent_body/kernel/  ← 孔位层 = 稳定契约（本项目）
   ├── port.py               BrainPort 抽象接口（ABC，覆盖大脑全能力面）
   ├── registry.py           内核注册表（插件机制：换内核只加一个 adapter）
   └── superbrain_adapter.py 大脑 → 孔位的桥（委托 superbrain2 门面）
   │
   ▼
agent_body/runtime.py  ← 身体：只依赖契约调用，注释写死"不 import 内核内部"
```

- **大脑 = 可替换内核**：任何实现 `BrainPort` 契约的适配器类都可注册。
- **身体 = 面板**：只与注册表 build 出的 `BrainPort` 交互，不关心具体内核。
- **分离更新**：大脑仓库演进时，只要保持门面契约，agent 不用动；换内核加一个 adapter。

## 为什么这么设计

| 问题 | 契约方案 |
|---|---|
| 大脑升级会连带 agent 改代码 | agent 只依赖 `BrainPort` 契约，契约稳定则 agent 不动 |
| 换内核要重写身体 | 加一个新 adapter 即可，`registry.register(name, cls)`，身体零改动 |
| 不完整内核静默崩 | `BrainPort` 是 ABC，未实现完整契约 → 无法实例化 → 注册时 `inspect.isabstract` 拦截 |
| 契约演进无感知 | `BRAIN_PORT_VERSION`，注册时版本不符 → 拒绝而非静默崩 |
| 多会话隔离被闭包破坏 | registry 只存「名字→类」，参数由 `build(name, **kwargs)` 每次显式传 |

## 用法

```python
from agent_body.kernel import BrainPort, BrainTool, default_registry

# 1. 内核已由 agent_body/_bootstrap 自动注册 "superbrain"
# 2. build 时显式传参（不同 session 各自独立实例）
port = default_registry().build(
    "superbrain", data_dir=".body-data", session="local", llm=None)
assert port.ready()
reply = port.chat("你好")
port.save(); port.close()

# 3. 洗卡：换内核 = 注册新 adapter，身体代码不用改
default_registry().register("my-kernel", MyKernelAdapter)
```

## 契约能力面（BrainPort）

- 会话/生命周期：`chat` · `remember` · `recall` · `save` · `load` · `close`
- 认知/人格/表达：`state` · `personality` · `set_personality` · `personality_mode`
  · `set_personality_mode` · `apply_style` · `style_text` · `humanize`
  · `set_user_style` · `user_style`
- 自主推进：`tick` · `generate_thoughts` · `drain_thoughts`
  · `generate_goals` · `adopt_goals` · `autonomous_goals`
- 记忆维护：`memory_count` · `index_concepts` · `deduplicate` · `orientations` · `user_profile`
- 孔位注入：`attach_tools`（身体把工具挂进大脑）· `set_permissions`（身体定权限）
- 版本/健康：`ready` · `version` · `health`

## 版本契约

- `BRAIN_PORT_VERSION = 1`
- 大脑接口演进导致契约变更 → 递增此号；`registry.register` 检测到不符拒绝注册，
  提示更新 adapter → 不静默崩。

## 测试

```bash
python -m unittest tests.test_brain_port -v   # 孔位层专项（版本保护/换内核/隔离/降级）
python -m unittest discover -s tests          # 全量回归
```