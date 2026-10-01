# MCE / BR-MCE 实验基线与 ACE 结论

本文记录两篇论文阅读后的基线判断，以及当前 `mce_clean_code` 后续实验应采用的对照体系。

参考论文：

- [Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models](https://arxiv.org/html/2510.04618v3)
- [Meta Context Engineering via Agentic Skill Evolution](https://arxiv.org/html/2601.21557)

## 1. 结论

**ACE 必须纳入实验基线，而且应作为主要基线之一。**

ACE 是 MCE 直接面对的前代 Agentic Context Engineering 方法。MCE 论文自己的实验将 ACE 与 Base Model、ICL、MIPROv2、GEPA、Dynamic Cheatsheet 一起比较，并将 ACE 作为最强的先前方法之一。MCE 论文报告 offline 平均相对提升为 89.1%，ACE 为 70.7%；online 设置中 MCE 为 74.1%，ACE 为 41.1%。

因此，如果 BR-MCE 只比较 Base、MCE 和 BR-MCE，就只能说明 BR-MCE 改进了 MCE，不能充分证明它超过已有的 Agentic Context Engineering 方法。

## 2. 两篇论文的关系

### ACE

ACE 将上下文视为持续演化的 playbook，核心机制包括：

- Generator 生成执行轨迹或候选经验；
- Reflector 从成功和失败中提炼可复用经验；
- Curator 将经验整理为结构化上下文；
- 增量 delta 更新，避免每轮完整重写；
- grow-and-refine，持续增长并进行去重和整理。

ACE 的上下文倾向于结构化、逐条累积的 playbook。其论文实验包含 Base LLM、ICL、MIPROv2、GEPA、Dynamic Cheatsheet 和 ACE。

### MCE

MCE 将优化对象从“上下文”进一步提升为“上下文工程技能”：

- Meta-Agent 根据任务、历史技能、执行结果和评估结果演化 `SKILL.md`；
- Base-Agent 执行技能；
- Base-Agent 可以生成上下文文件、代码和动态上下文函数；
- 上层优化技能，下层优化上下文产物，形成双层迭代。

MCE 的主要方法差异是技能可演化、上下文产物形式开放，不再固定为 ACE 式的结构化条目。

## 3. 推荐的完整基线矩阵

| 类别 | 方法 | 作用 |
|---|---|---|
| 基础能力 | Base LLM | 不使用学习上下文，测量模型原始能力 |
| 简单上下文 | ICL / Fixed Context | 判断收益是否只是来自示例或固定提示 |
| 现有 CE 方法 | ACE | 与最重要的前代 Agentic CE 方法比较 |
| 现有 CE 方法 | GEPA | 代表反思式、偏简洁的 Prompt 优化 |
| 现有 CE 方法 | MIPROv2 | 代表 Prompt 与示例联合优化 |
| 现有 CE 方法 | Dynamic Cheatsheet | 代表在线外部记忆累积 |
| 原始方法 | MCE-Full | 当前 MCE 标准配置 |
| 公平控制 | MCE-Budget-Matched | 使用 BR-MCE 相同预算的 MCE |
| 公平控制 | ACE-Budget-Matched | 使用 BR-MCE 相同预算的 ACE |
| 本文方法 | BR-MCE | 预算感知搜索、收益路由与风险控制 |

## 4. 最小可发表基线

如果算力有限，第一阶段至少运行：

```text
Base LLM
ICL 或 Fixed Context
ACE-Budget-Matched
MCE-Full
MCE-Budget-Matched
BR-MCE
```

如果资源允许，增加 GEPA、MIPROv2 和 Dynamic Cheatsheet。它们对于解释不同上下文归纳偏置很有价值：GEPA 偏向简洁提示，ACE 偏向积累式上下文，MCE 允许 Agent 自行决定上下文文件和动态代码的结构。

## 5. ACE 需要运行两个版本

### ACE-Full

按照 ACE 论文和官方实现的推荐设置运行，用于文献级别的可比性。它可以保留较高的 epoch、反思轮数和逐样本更新策略。

### ACE-Budget-Matched

将 ACE 限制在 BR-MCE 的统一预算内：

- 相同训练输入 token 上限；
- 相同训练输出 token 上限；
- 相同模型调用次数上限；
- 相同工具调用和 Embedding 预算；
- 相同 wall-clock 或 GPU 时间约束；
- 相同推理平均上下文 token 预算。

ACE-Full 负责回答“原论文方法在本环境中的能力如何”；ACE-Budget-Matched 负责回答“在相同成本下 BR-MCE 是否更有效”。不能用 ACE-Full 的高成本结果直接与 BR-MCE 的低成本结果做公平优劣结论。

MCE 同样需要 MCE-Full 和 MCE-Budget-Matched 两个版本。后者是检验 BR-MCE 净方法增益的关键控制组。

## 6. 不应混淆的类别

以下属于外部基线：

- Base LLM；
- ICL；
- ACE；
- GEPA；
- MIPROv2；
- Dynamic Cheatsheet。

以下属于 MCE 或 BR-MCE 的消融，不是外部基线：

- MCE without skills；
- MCE with fixed skill；
- BR-MCE without cost objective；
- BR-MCE without risk objective；
- BR-MCE without sample-level router；
- BR-MCE without multi-fidelity search；
- 整体技能重写与模块化技能编辑的比较。

## 7. 当前仓库推荐的实验顺序

当前仓库的任务环境和主流程更接近 offline 迭代优化，因此建议先完成 offline 基线：

```text
训练集：用于上下文/技能优化
验证集：用于候选选择与早停
测试集：冻结后只评估一次
```

第一轮只使用症状诊断单步环境和 `llama-3.1-8b`，不要同时扩展任务、模型和 online 协议。

之后再实现 online 协议：

```text
当前样本先预测
→ 记录首轮结果
→ 用该样本反馈更新上下文
→ 处理下一个样本
```

offline 与 online 必须分开报告，不能把训练后评估结果和逐样本首轮结果放到同一张主表中。

## 8. 公平性要求

ACE、MCE 和 BR-MCE 必须统一以下条件：

- 相同基础模型、模型版本和服务端配置；
- 相同 system prompt 与初始上下文；
- 相同训练、验证、测试划分；
- 相同样本顺序和随机种子；
- 相同 temperature、最大输出长度和上下文上限；
- 相同缓存策略；
- 相同成本记账方式；
- 相同失败、超时和重试处理。

所有方法的 Meta/Generator/Reflector/Curator/评估请求都要计入账本。不能让某个方法使用预计算上下文而不计入构建成本。

ACE 的本地复现应优先使用与 BR-MCE 相同的 `llama-3.1-8b`。如果没有严格复现官方实现，应将结果命名为 `ACE-reimplementation` 或 `ACE-adapter`，不能直接宣称为原始 ACE。

当前单个 LLaMA 服务足以承担 ACE 的 Generator、Reflector 和 Curator，不应给 ACE 额外使用更强的云端模型，否则比较会混入模型能力差异。

## 9. 论文主张的最低证据

BR-MCE 至少应回答：

1. 在相同训练预算下，BR-MCE 是否优于 MCE 和 ACE？
2. 在相同推理预算下，BR-MCE 是否达到更高性能？
3. 达到相同性能时，BR-MCE 是否需要更少模型调用或 token？
4. ACE 的长上下文累积与 BR-MCE 的动态路由在负上下文收益率上有什么差异？
5. MCE 的提升在严格预算后还剩多少，而不是仅仅来自更多搜索和更长上下文？

主结果建议报告性能—训练成本、性能—推理成本和性能—风险三类曲线，而不只报告单点准确率。

## 10. 最终判断

**ACE 必须包括。**

最简方案可以暂时不实现 MIPROv2 和 Dynamic Cheatsheet，但不建议省略 ACE。因为 BR-MCE 的研究对象正是 MCE 的预算、风险和适用边界；ACE 是判断该方法是否超过现有 Agentic Context Engineering 方法的必要参照。
