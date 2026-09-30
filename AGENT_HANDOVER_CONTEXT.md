# MCE 本地化部署与模型替换任务：上下文交接文档

## 1. 项目背景 (Context)
你接手的项目是 **Meta Context Engineering (MCE)**。这是一个双层 Agent 架构的系统，用于自动演进 Prompt 和生成任务相关的上下文。
- **Meta-Agent**：根据迭代历史，生成 `SKILL.md` 指导下层 Agent。
- **Base-Agent**：阅读 `SKILL.md` 和训练数据，编写 Python 代码（`interfaces/*.py`）和 Markdown 上下文资源。
- **Eval (评估模块)**：使用 Base-Agent 生成的函数和上下文，对大量样本进行并发测试，统计准确率。

**当前业务目标**：出于成本和本地化集群部署的需求，我们需要将整个项目迁移到**本地受限算力集群（如双卡 160GB）**上，并将所有模型（生成、评估、Embedding）统一替换为本地的小参数模型（如 `Llama-3.1-8B-Instruct` 和 `Qwen3-Embedding-0.6B`）。

---

## 2. 已完成的工作 (What has been done)
前一个 Agent 已经完成了 10 项基础架构与稳定性的改造（目前代码库已是最新状态）：
1. **环境变量接入**：在 `.env` / `utils` 中将模型名称、并发数、超时等参数完全抽离为 `MCE_*` 环境变量。
2. **集群稳定性**：修改了 `eval.py` 和 `llm.py` 中的高并发控制，防止 vLLM 的 KV Cache OOM。
3. **准确率计算修复**：修复了原代码中吞咽报错样本导致准确率虚高的核心 Bug。
4. **小模型容错增强**：
   - 在 `mce/validation.py` 中引入了运行时的 Dummy 冒烟测试。
   - 将 Agent 的校验重试次数上限提升至 5 次。
   - 降级了提示词要求，强制约束 Agent 使用 `Write` 工具全量覆盖文件，禁止使用易错的 `Edit` 字符串替换工具。

---

## 3. 当前面临的困境与核心阻塞点 (The Blocker)
虽然基础配置已改完，但系统**仍然无法一键运行**。核心卡点在于底层的 Agent 引擎绑定：

- **现状**：代码库中的 `mce/base_agent.py` 和 `mce/meta_agent.py` 强依赖了一个名为 `claude-agent-sdk` 的第三方库。
- **底层机制**：这个 SDK 在底层会 `spawn` 唤起一个官方的闭源 `claude` CLI 进程。
- **协议冲突**：这个闭源 CLI **写死了只支持 Anthropic 的 Messages API 格式**（`/v1/messages`）。而我们的本地集群（无论是 vLLM 还是 TGI）对外暴露的都是 **OpenAI 格式**（`/v1/chat/completions`）。

由于闭源 CLI 无法修改其请求协议，导致虽然 `eval.py` (纯推理) 可以无缝对接本地 Llama 8B，但 **Agent 循环部分必定会报错阻断**。

---

## 4. 你的任务指令 (Instructions for You)
你不必考虑使用 LiteLLM 之类的代理层（因为小模型处理 Anthropic 和 OpenAI 之间复杂的 Tool Calling 互相翻译容易出现不可靠的行为）。

**你的核心任务是：替换底层 Agent 框架 (Framework Swap)**。

请执行以下步骤彻底解耦 Anthropic：
1. **废弃 `claude-agent-sdk`**：
   - 彻底移除 `mce/base_agent.py` 和 `mce/meta_agent.py` 中对 `claude_agent_sdk` 的调用逻辑。
2. **重写 Agent Loop**：
   - 引入一个支持原生 OpenAI 协议的轻量级 Agent 框架。你可以使用代码库已经存在的 `langchain-openai` (搭配 `langgraph` 或原生的 `create_tool_calling_agent`)，或者非常轻量的 `smolagents`。
3. **实现核心 Tool**：
   - 根据原逻辑，用 Python 函数为新 Agent 重新注册工具：至少需要实现 `Read` (读文件) 和 `Write` (写文件) 两个工具。确保它们受到与原先 `permission_handler` 同等的沙箱路径限制（只能读写当前迭代的目录）。
4. **对接多轮校验**：
   - 确保重写后的 Agent 依然能正确对接 `mce/validation.py` 里的多轮校验重试循环（如果校验失败，将 `format_validation_feedback` 塞回给模型让它继续修改）。

请保证你的修改：**完全兼容本地 OpenAI 标准接口，并且让 Llama 8B 能够凭借原生 Tool Calling 能力跑通整个 Context 演进流程。**
