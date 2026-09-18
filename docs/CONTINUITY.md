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
每轮 chat 结束 → 记录该轮真实输入 token（压缩后）
  → ContextBudget 更新 per-turn 高水位 last_input / max_input
  → 若 last_input / context_length >= continuity_at（默认 0.5，Hermes 同款触发线）
       → ContinuityManager 触发：
           1. 生成精确锚点（原词句，非摘要）
           2. 写向量记忆（tags=[continuity, session]）—— 后继会话可精确召回
           3. 返回 successor_session（如 <当前>#2）
           4. 返回 continuity note（供网关/调用方启动新会话）
  → 新会话首轮：handover/briefing 自动注入锚点简报（已具备）
       → 无缝：新会话知道上一会话在哪、做过什么、下一步是什么
```

## 5. 落地清单

**super-agent（body，自包含可测）**
- [ ] `model_router.py`：OpenAIProvider 抓 `usage`（委派路径真实数）✅ 已做
- [ ] `budget.py`：ContextBudget 升级——跟踪 per-turn 真实输入（last/max_input）+ 区分累计成本，`over_compressed` 按输入占窗口比例
- [ ] `continuity.py`：ContinuityManager——阈值判断 + 精确锚点生成 + 后继会话 + 写向量记忆
- [ ] `runtime.py`：chat 结束记录真实输入 + 触发 continuity 检查
- [ ] `__main__.py`：CLI `/continuity` 查看状态/手动触发
- [ ] 测试 + 文档

**superbrain-2.0（内核，压缩真相源）**
- [ ] `core/llm.py`：`LLMResponse` 补 `prompt_tokens/completion_tokens/total_tokens`，`chat()` 解析 `usage`
- [ ] `core/agent.py` / `facade.py`：把当轮 usage 暴露（如 `last_usage` / 会话累计）
- [ ] super-agent `kernel/superbrain_adapter.py` + `port.py`：BrainPort chat 透出 usage

## 6. 验证纪律
- 用真实 provider 跑一轮，断言 `usage.prompt_tokens` 被正确捕获（非 0、> 精简 prompt 的本地估算）
- 触发线：构造超大输入 → 断言触发 + 锚点写入向量库 + 后继会话可召回锚点
- 语义保持：把一段长文写入 → 压缩后 `recall` 能原样取回关键句子（不靠模型）
