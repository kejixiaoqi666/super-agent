# 自动续接 Auto-Continuity 设计（长上下文无缝衔接 + 严格语义保持）

> 目标：上下文太长时**自动开新会话**并**无缝接上**上一个，衔接"不丢语义、不偏移、不幻觉"，
> 且触发依据是**向量精自压缩后的真实输入 token**，而非累计上下文。

## 1. 为什么和 Hermes 不同（差异化）

| 维度 | Hermes（参考） | 我们（super-agent + superbrain 向量库） |
|---|---|---|
| 长上下文处理 | **模型生成压缩摘要**（有损，可能偏移/幻觉） | **向量召回原文**（`store.search` 精自压缩，语义无损，细节可精确重检索） |
| 触发依据 | 上下文约 50% | **每轮真实输入 token** 占窗口比例（压缩后，非累计） |
| 衔接 | 摘要 + session_search | **精确锚点**写入向量记忆 + 后继会话自动召回 |

Hermes 把早期对话"压成摘要"——摘要由模型生成，必然有损、可能跑偏。我们用向量库：
- 记忆按语义分块入库，召回时只取相关块 → **精自压缩**（token 确实变少且聚焦）。
- 任何被"压缩"掉的细节，都能用向量检索**精确召回原文**，不需要模型脑补 → **不幻觉**。
- 自动续接时写入的是**精确锚点**（任务/决定/文件/阻断项的原词句），不是模型总结 → **不偏移**。

## 2. 度量真相：真实输入 token 在哪成型、被谁丢弃

```
每轮：
  你的 message → Body.chat → curator 精简 prompt
   → brain.chat(prompt)  [superbrain Agent.chat]
       → 组装 msgs = 系统提示 + 状态 + 向量召回 mem_block + 工具 + prompt   ← 真实输入在此成型
       → llm.chat(msgs) → provider 返回 usage.prompt_tokens                 ← 真实数
  目前 usage 被丢弃：super-agent/model_router.py + superbrain/core/llm.py 都只回 content。
```

**结论**：要拿"确实压缩后的 token"，必须在 `superbrain/core/llm.py` 的 `LLMResponse` 补 `usage`，
从 `Agent.chat` 一路穿回 Body 的 `ContextBudget`。这是准确触发自动续接的前提。

## 3. 严格约束的落地映射

| 要求 | 落地机制 |
|---|---|
| 不丢语义 | 向量召回只取相关块；需要时 `recall` 精确取回原文，不生成替代摘要 |
| 不偏移 | 续接锚点=原词句（任务/决定/文件路径/阻断项/用户偏好），非模型改写 |
| 不幻觉 | 锚点指向可检索记忆，绝不靠模型"回忆"生成内容；无匹配就不写 |
| token 是压缩后的 | 度量点 = `llm.chat(msgs)` 的真实 `usage.prompt_tokens` |

## 4. 自动续接流程

```
每轮 chat 结束 → 记录该轮真实输入 token（压缩后，内核 usage）
  → ContextBudget 更新 per-turn 高水位 last_input / max_input
  → 分级：input_pct < warn_at → ok；≥ warn_at → warn(预动预警, 建议留意)；
          ≥ continuity_at → critical(应续接新会话)
  → critical：ContinuityManager 触发：
           1. 生成精确锚点（原词句，非摘要）
           2. 写向量记忆（tags=[continuity, session]）—— 后继会话可精确召回
           3. 返回 successor_session（如 <当前>#2） + 进入冷却(600s, 仅写入成功)
  → Body.continuity_advice(session)：可执行闭环——critical 才落盘, warn/ok 轻量咨询
  → 新会话首轮：handover/briefing 自动注入锚点简报 → 无缝
```

**预动性**：预警线 warn_at = continuity_at × warn_ratio(默认0.8)，在真正到阈值前就提醒，
用户可主动 `/new` 续接，而非被动临界才动作。

## 5. 落地清单

**super-agent（body，自包含可测）**
- [x] `model_router.py`：OpenAIProvider 抓 `usage`（委派路径真实数）✅ 已做
- [x] `budget.py`：ContextBudget 升级——跟踪 per-turn 真实输入（last/max_input）+ 区分累计成本，`over_compressed` 按输入占窗口比例
- [x] `continuity.py`：ContinuityManager——阈值判断 + 精确锚点生成 + 后继会话 + 写向量记忆 + **冷却防刷屏(600s)**
- [x] `runtime.py`：chat 结束用**内核真实 usage** 记账（覆盖代理估算）+ 触发 continuity 检查
- [x] `__main__.py`：CLI `/continuity` 查看状态
- [x] 测试 318 全绿 + 文档

**superbrain-2.0（内核，压缩真相源）**
- [x] `core/llm.py`：`LLMResponse` 补 `prompt_tokens/completion_tokens/total_tokens`，`chat()` 解析 `usage`
- [x] `core/agent.py`：`_run_tool_loop` 累计真实用量（单次输入取 max=最坏当轮输入，每轮重置）+ `usage()`
- [x] `facade.py`：`SuperBrain.usage()` 透出
- [x] super-agent `kernel/superbrain_adapter.py` + `port.py`：BrainPort `usage()`（可选契约，默认 0 兜底）

## 6. 验证纪律
- [x] 内核单测：FakeLLM usage→`agent.usage()` 贯通且每轮重置
- [x] 全链 facade：`SuperBrain.usage()` 返回真实 prompt_tokens
- [x] super-agent：`Body.chat` 用真实 usage 记账（FakePort usage=900 → last_input≥900），无 usage 时回退身体精简 prompt 代理
- [x] 语义保持：把一段长文写入 → 压缩后 `recall` 能原样取回关键句子（不靠模型）——已用真实内核+hashing 库跑通，5/5 事实原样召回；语义回归测试固化在 tests/test_semantic_recall.py
