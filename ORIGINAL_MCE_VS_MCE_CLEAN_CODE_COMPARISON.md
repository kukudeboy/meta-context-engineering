# 官方 MCE 与 `mce_clean_code` 代码对比报告

## 1. 对比对象

官方仓库已克隆到桌面：

```text
/home/k/桌面/meta-context-engineering-github
```

来源：

```text
https://github.com/metaevo-ai/meta-context-engineering.git
```

本次克隆的官方 `main` 快照：

```text
c4b7a7c Update paper title and badge in README
```

当前工作区：

```text
/home/k/桌面/mce_clean_code
```

两者分开保存，官方源码没有覆盖当前工作区。官方仓库有 41 个 Git 跟踪文件，当前工作区有 53 个 Git 跟踪文件，并另外包含部署文档、测试和本地适配文件。

## 2. 总体结论

当前 `mce_clean_code` **保留了 MCE 的外层双层优化机制**，但并不是“只替换模型和 Claude SDK”的纯等价移植。

可以分成三类变化：

1. **必要的运行时替换**：Claude Agent SDK → 自研 OpenAI 兼容 `ToolAgent`，云端模型 → 集群 vLLM/Embedding 服务。
2. **部署和稳定性适配**：并发、超时、配置优先级、路径、Embedding 端口、离线启动、语法/冒烟校验等。
3. **会影响 Agent 行为或实验结果的额外变化**：工具集合缩减、Write-only 文件修改提示、SKILL 内联、校验重试次数增加、评估错误计入分母、Agent 诊断回退回答等。

因此，结论是：

> **MCE 的“迭代—Meta-Agent—Base-Agent—评估—技能归档”核心外层流程基本保持一致；但 Agent 执行能力、工具行为、校验策略和评估统计已经发生了实质变化，不能严格声称只有模型和 SDK 不同。**

## 3. 文件级差异概览

### 官方仓库中不存在、当前工作区新增的核心文件

| 文件 | 作用 |
|---|---|
| `mce/agent.py` | OpenAI 兼容 Tool Calling Agent、沙箱、Read/Write/Glob/Bash 工具和多轮循环 |
| `tests/test_cluster_runtime.py` | 集群客户端、Embedding、事件循环和模板回归测试 |
| `scripts/run_cluster_smoke.sh` | 断网集群直接调用已有 Python 环境的冒烟脚本 |
| `assets/tool_chat_template_llama3.1_json.jinja` | vLLM 0.8.5 Llama 3.1 工具调用模板 |
| `assets/VLLM_LICENSE` | 模板对应许可证 |
| `CLUSTER_RUN.md` | 集群部署和运行说明 |
| `llm_deployment_summary.md` | 集群模型服务部署记录 |
| `BR_MCE_PHASED_IMPLEMENTATION_PLAN.md` | BR-MCE 分阶段实现计划 |
| `MCE-改进点.md` | BR-MCE 设计文档 |
| `requirements.txt`、`reqs_311.txt` | 离线依赖清单 |

### 官方存在、当前基本未改变的核心文件

- `mce/logging_utils.py` 的主要代码保持一致，虽然其中仍有 Claude SDK 相关历史命名。
- `env/registry.py` 的任务注册方式保持一致。
- `mce/main.py` 的大部分迭代编排保持一致。
- 任务的 `InterfaceSignature`、`TaskEnvironment`、接口加载与上下文产物目录仍沿用原设计。

## 4. MCE 外层核心机制是否改变

### 4.1 基本保持一致的部分

当前 `mce/main.py` 与官方版本的主要控制流一致：

```text
iteration 0 基线评估
→ 创建 iteration/sub-iteration workspace
→ Meta-Agent 生成或演化 SKILL.md
→ 按 batch 评估训练样本
→ 保存 train.json
→ Base-Agent 根据技能和结果修改 context/interfaces
→ 接口校验
→ 下一批次复制上一批次产物
→ 最终 validation
→ 聚合 evaluations.json
→ 归档最终 SKILL.md
```

`mce/main.py` 相对官方版本的主要改动只有：

- `load_dotenv(override=True)` 改为不覆盖外部环境变量；
- CLI 默认模型改为读取 `MCE_MODEL`；
- 原始默认模型从 DeepSeek 改为本地配置/兼容默认值。

所以，**Meta-Agent 与 Base-Agent 的双层迭代关系、sub-iteration 机制、技能归档和验证集选择逻辑没有被改写。**

### 4.2 不再严格等价的部分

虽然外层流程不变，Agent 执行层已经变化：

- 官方使用 Claude Agent SDK 的 `ClaudeSDKClient` 和 `ClaudeAgentOptions`。
- 当前使用 `mce.agent.ToolAgent`，通过 OpenAI `/v1/chat/completions` 发起工具调用。
- 官方依赖 SDK 的消息流、工具注册、项目技能加载和权限回调。
- 当前实现自己解析结构化 tool calls 和文本 JSON tool calls。
- 当前每轮最多执行一个工具调用，以适配 Llama 3.1 工具模板。
- 当前会裁剪历史消息以适配上下文窗口。

这不是 MCE 搜索机制的改变，但会改变 Agent 的实际执行轨迹、工具调用次数和生成结果。

## 5. Meta-Agent 的差异

### 官方行为

官方 Meta-Agent：

- 通过 Claude Agent SDK 工作；
- 允许较宽的工具集合，包括 `Read`、`Write`、`Edit`、`Bash`、`Glob`、`Grep` 等；
- 使用权限回调限制读写范围；
- 最多 3 次 `SKILL.md` 生成校验；
- SDK 负责项目技能加载和消息收集。

### 当前行为

当前 Meta-Agent：

- 使用 `ToolAgent`；
- 工具集合实际限制为 `Read`、`Write`、`Glob`；
- 沙箱允许读取 workspace，但只允许写入当前 iteration 的 `.claude/skills/`；
- 最大校验次数由 `MCE_MAX_VALIDATION_ATTEMPTS` 控制，当前默认 5 次；
- 使用明确的 `next_prompt` 把缺失 `SKILL.md` 的反馈重新交给同一个 ToolAgent 对话；
- 不再使用 Claude SDK 的 `Edit`、`Grep`、`Task` 等工具。

### 对核心机制的影响

Meta-Agent 仍然在做“读取历史技能与评估 → 生成新技能”，所以 MCE 的元层目标没变。

但工具集合减少会限制它的搜索和检查能力。尤其是：

- 没有 `Edit`，只能依赖完整 `Write`；
- 没有 `Grep`，只能通过 `Read` 或 `Glob` 查找；
- 没有 `Task`/子 Agent；
- 没有 Claude SDK 自动项目技能加载，当前代码通过 Base-Agent 内联 SKILL 内容解决，Meta-Agent 则依赖提示词和文件工具。

这些属于运行时替换带来的行为差异，而不是单纯模型名称差异。

## 6. Base-Agent 的差异

### 保持的部分

- 仍读取任务说明、接口签名、训练结果和 `SKILL.md`；
- 仍修改 `context/` 和 `interfaces/`；
- 仍使用 `validate_interfaces` 做签名、语法、导入和返回值校验；
- 仍在校验失败时将反馈送回 Agent；
- 仍在成功后归档并清理无关文件。

### 发生的变化

- Claude SDK 权限处理改为 `Sandbox`；
- 工具集合改为 `Read`、`Write`、`Glob`，可选 `Bash`；
- 禁止写入 `utils/`；
- 将 `SKILL.md` 内容直接追加到 Base-Agent 初始 prompt，减少一次 Read 调用；
- 校验最大次数从 3 改为环境变量，当前默认 5；
- 当前 prompt 明确要求使用 `Write` 完整覆盖文件，不使用 `Edit`；
- Agent 结果由 `ToolAgent.query()` 返回，而不是 Claude SDK 的消息流。

### 影响

“Base-Agent 执行技能并生成上下文产物”的角色没有改变，但小模型执行行为明显被重新约束：更少工具、更强的完整文件写入要求、更长的校验重试预算。这些调整可能提升本地 Llama 8B 的成功率，但也会改变与官方 MCE 的成本和行为对比。

## 7. 任务评估与统计差异

### 7.1 模型和并发配置

当前 `mce/llm_client.py`：

- 从 OpenRouter/OpenAI 环境变量改为 `DASHSCOPE_API_BASE` 和 `DASHSCOPE_API_KEY`；
- 支持本地 vLLM 地址；
- 从环境变量读取超时和最大输出 token；
- 增加 `max_tokens` 限制；
- 日志记录模型和 endpoint。

这些是部署适配，但会改变解码和超时行为，需要在实验报告中记录。

### 7.2 并发与错误分母

当前 `mce/eval.py`：

- 并发数从固定 30 改为 `MCE_MAX_CONCURRENCY`；
- 平均指标计算使用所有样本数作为分母，包含程序错误样本。

第二项是统计语义变化。官方版本只对成功结果求平均，可能导致错误样本被排除、准确率偏高；当前版本将错误按 0 分计入，统计更保守。

因此，当前结果不能直接与官方仓库历史结果按数字比较，必须说明两者的错误处理口径不同。

## 8. 接口校验差异

当前 `mce/validation.py` 额外加入运行冒烟测试：

- 为函数签名构造 dummy 参数；
- 实际调用接口函数；
- 捕获 `TypeError`、`NameError`、`FileNotFoundError` 等运行错误；
- 将 traceback 放入校验反馈。

官方版本主要检查文件、AST、函数签名、导入和 return。当前校验更严格，也可能拒绝官方版本原本会接受的“导入成功但 dummy 输入不能运行”的接口。

这不是 MCE 双层机制变化，但属于 Base-Agent 成功判定条件变化。

## 9. Prompt 与技能内容差异

当前 Base-Agent prompt 新增：

- 必须使用 `Write` 完整写文件；
- 不使用 `Edit`；
- 读取原文件后整体覆盖；
- Bash 只运行简单命令。

当前 Meta-Agent prompt 新增：

- `SKILL.md` 少于 800 词；
- 使用短句和编号步骤；
- 避免长段落。

这些提示词不是纯模型配置，它们会影响技能搜索空间和 Base-Agent 的实现方式。尤其是 `SKILL.md` 长度限制会改变 Meta-Agent 的搜索结果，应在论文实验中固定并记录。

## 10. Embedding 与路径适配

当前版本新增或修改：

- Embedding 模型从 OpenAI embedding 改为环境变量指定的 `qwen-embed`；
- Embedding endpoint 独立使用 8001；
- `check_embedding_ctx_length=False`；
- `encoding_format=float`；
- workspace 工具目录从固定的 `workspace_base.parent.parent` 改为基于 `mce/utils.py` 的 `__file__` 定位。

这些修改不改变 MCE 的“静态知识 + 动态检索/组合”抽象，但会改变检索模型、向量结果和自定义 workspace 的文件复制行为。

## 11. Agentic 任务环境差异

官方 Agentic 症状诊断环境使用 Claude Agent SDK；当前环境使用 `ToolAgent`：

- 工具改为只读 `Read`、`Glob`；
- 最多 10 个 Agent turns；
- 若结果中没有 `[DIAGNOSIS]`，追加一次无工具最终回答请求；
- 日志保留 Agent 文本轨迹；
- 错误记录 traceback；
- 上下文接口在单步和两步环境中通过 `asyncio.to_thread` 执行，避免阻塞主事件循环。

诊断任务的输入、输出格式和准确率比较逻辑仍然保持，但 Agent 的决策轨迹和最终回答回退行为发生了变化。

## 12. “除了模型和 Claude SDK 外是否都一致”判断

### 可以认为保持一致的部分

- MCE 双层结构；
- Meta-Agent 负责技能演化；
- Base-Agent 负责上下文工程；
- iteration/sub-iteration 学习流程；
- 训练批次评估与 `train.json`；
- `context/`、`interfaces/`、`.claude/skills/` 产物结构；
- InterfaceSignature 驱动的接口校验；
- 最终 validation 与技能归档；
- TaskEnvironment / EnvironmentRegistry 抽象。

### 不能说完全一致的部分

- 工具集合和工具调用协议；
- SKILL 自动加载与内联方式；
- Base/Meta-Agent 校验重试次数；
- Base-Agent 文件写入方式；
- 接口运行冒烟测试；
- 错误样本的准确率分母；
- 并发、超时和最大输出 token；
- Embedding 模型和 endpoint；
- Agentic 评估的最终回答回退逻辑；
- Prompt 对技能长度和文件修改方式的额外约束。

## 13. 为了严格公平比较，建议区分两个版本

### 部署稳定版

保留当前版本的本地集群修复：

- OpenAI 兼容 ToolAgent；
- Llama 3.1 tool calling；
- Qwen Embedding；
- 并发和超时保护；
- 错误计入分母；
- 运行冒烟校验。

它适合真实部署和本地集群运行。

### 论文等价版

为了比较“方法本身”而不是部署修复，建议额外提供实验配置开关，使以下行为可复现并固定：

- 官方 MCE 的 iteration、batch 和 validation 设置；
- 固定模型调用次数和 token 上限；
- 明确错误样本分母口径；
- 固定是否内联 SKILL；
- 固定工具集合；
- 固定校验重试次数；
- 固定 prompt 长度约束；
- 固定 Embedding 结果和缓存策略。

主论文中的 MCE 基线应使用统一预算和统一统计口径；官方仓库历史结果只能作为参考，不能直接作为严格对照数字。

## 14. 最终结论

当前 `mce_clean_code` 不是对官方 MCE 的简单“换模型 + 去掉 Claude SDK”版本，而是：

> **保留 MCE 核心双层优化机制，同时加入本地 OpenAI/vLLM 运行时、工具沙箱、弱模型容错、错误统计修复、Embedding 分离和部署稳定性改造的工程分支。**

如果目标是“保持论文方法完全一致”，需要保留当前 ToolAgent 作为协议替代，但把工具集合、校验次数、提示词限制、错误分母和评估预算做成可配置，并为官方等价配置建立独立实验模式。
