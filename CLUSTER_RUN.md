# 离线集群运行与变更摘要

本次适配 `llm_deployment_summary.md` 中的 vLLM 0.8.5 双服务方案：
LLaMA 服务 `llama-3.1-8b` 在 8000 端口，Qwen 服务 `qwen-embed` 在 8001 端口。
默认 MCE 和两个服务位于同一节点；跨节点时修改 API 地址。

## 修改文件

| 文件 | 变更 |
| --- | --- |
| `.env`、`.env.template` | 本地服务地址和名称；并发 2；Agent 输入预算 24000、输出预算 4096；普通推理输出上限 1024 |
| `mce/utils.py` | 独立 Embedding 配置及旧配置 fallback；发送原始文本和 float 向量；用 `__file__` 定位源码 |
| `mce/workspace_utils/embedding.py` | 使用独立的 8001 服务，禁用客户端 token 切分，明确 float 编码 |
| `mce/workspace_utils/llm.py` | 文本直接推理；schema 默认显式 function calling 并验证输出；已有事件循环时使用独立线程和事件循环，按批次创建并关闭异步 HTTP transport |
| `mce/main.py`、`mce/eval.py` | `--model` 默认读取 `MCE_MODEL` |
| `mce/agent.py`、`mce/llm_client.py` | 记录实际模型和服务地址；Agent 连接失败保留原始异常链；限制推理输出长度 |
| `mce/base_agent.py`、`mce/meta_agent.py`、`env/base.py` | `.env` 不覆盖已导出的环境变量；其他调用 dotenv 的文件同样调整 |
| 单步、两步诊断环境 | 在线程中运行同步上下文函数，避免阻塞评估事件循环 |
| 三份 `scripts/train_symptom_diagnosis*.sh` | 移除 Bash 提前选择云端模型的默认参数，由 Python 加载配置后决定 |
| `scripts/run_cluster_smoke.sh` | 直接调用已有 Python 环境；5 条训练、5 条验证、1 轮；默认生成新的输出目录，可追加 CLI 参数 |
| `assets/tool_chat_template_llama3.1_json.jinja`、`assets/README.md`、`assets/VLLM_LICENSE` | vLLM 0.8.5 官方原始聊天模板、来源和许可证 |
| `pyproject.toml` 及依赖清单 | 显式声明 numpy/httpx；开发依赖补充用于模板测试的 Jinja2 |
| `tests/test_cluster_runtime.py` | 使用模拟 HTTP 验证文本/结构化输出、事件循环、Embedding 路由与原始文本、配置优先级、任意工作目录、聊天模板 |

配置优先级：CLI 参数 > 系统环境变量 > `.env` > 代码默认值。
`--model` 控制诊断评估模型；Agent 的专用设置仍由 `MCE_AGENT_MODEL` 控制。
同步 `call_llm` 可以在已有事件循环中执行，但会等待返回；异步代码优先
`await call_llm_async(...)`。自定义环境调用同步接口时应使用 `asyncio.to_thread`。

## 打包与环境准备

上传源码、`env/` 数据、`assets/` 模板和配置。`.env` 被 Git 忽略，
`git archive HEAD` 也不包含未提交修改或新增文件；请打包当前文件，或提交后
打包并在集群中通过 `cp .env.template .env` 生成配置。

集群已有 Python 环境应提供 `openai`、`python-dotenv`、`pydantic`、
`langchain-core`、`langchain-openai`、`httpx`、`numpy`。
如缺依赖，在联网机器准备兼容的离线依赖或独立客户端环境，不改变已修复的
vLLM/torch/transformers 服务环境。启动脚本不会安装依赖。

## LLaMA 服务启动

将 `task_mce_root` 改为集群上上传后的项目目录。以下为文档方案 B 的单卡启动参数；
使用已有模型服务时检查其启动参数，不要重复启动占用同一端口。

```bash
task_mce_root="$HOME/liyahui/MCE/mce-cluster"
CUDA_VISIBLE_DEVICES=0 nohup "$HOME/liyahui/miniconda/my_vllm_env/bin/python" \
  -m vllm.entrypoints.openai.api_server \
  --model "$HOME/liyahui/model/Meta-Llama-3.1-8B-Instruct" \
  --served-model-name llama-3.1-8b \
  --port 8000 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 32768 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser llama3_json \
  --chat-template "$task_mce_root/assets/tool_chat_template_llama3.1_json.jinja" \
  > "$HOME/liyahui/model/vllm_llama.log" 2>&1 &
```

Embedding 服务沿用部署文档的 GPU 1 / 8001 启动方案，模型名称必须为 `qwen-embed`。

## 检查与小规模训练

从运行 MCE 的节点执行，确认返回模型名称一致：

```bash
curl --connect-timeout 5 --max-time 10 http://127.0.0.1:8000/v1/models
curl --connect-timeout 5 --max-time 10 http://127.0.0.1:8001/v1/models
curl --connect-timeout 5 --max-time 120 http://127.0.0.1:8001/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-embed","input":["患者症状"],"encoding_format":"float"}'
```

运行脚本使用 `$HOME/liyahui/miniconda/my_vllm_env/bin/python`，也可通过
`MCE_CLUSTER_PYTHON` 指定已准备好的独立客户端环境；可以从任意目录启动：

```bash
cd "$HOME/liyahui/MCE/mce-cluster"
nohup bash scripts/run_cluster_smoke.sh > mce_cluster_smoke.log 2>&1 &
```

默认 workspace 名称含时间和进程号，避免失败重跑时遇到 `iter1_sub0` 已存在。
可设置 `MCE_CLUSTER_WORKSPACE`，也可在脚本后追加 `--workspace`、`--model`、
`--train-limit` 等参数覆盖默认值。终端日志为 `mce_cluster_smoke.log`，详细
Agent 日志位于 `logs/symptom_diagnosis_cluster/run_*/`。

## 本地检查

在具备项目依赖的环境中执行：

```bash
python -m unittest discover -s tests -v
bash -n scripts/run_cluster_smoke.sh
```

回归检查的 HTTP 响应均为模拟数据，不验证集群网络、GPU 服务启动或模型效果。

本次本地验证结果：13 个修改或新增的 Python 文件通过 `py_compile`；
4 个 Shell 脚本通过 `bash -n`；11 项回归检查全部通过；
离线启动脚本使用测试解释器时能正确进入 CLI 帮助；`git diff --check` 通过。

## 行为兼容模式

默认 `MCE_BEHAVIOR_PROFILE=cluster_safe`，适合 Llama 3.1 8B 集群运行：限制工具集合、要求完整 Write、最多 5 次接口校验、启用诊断最终回答回退，并将执行错误计入总体指标。

如需尽量接近原版 MCE 的 Agent 行为，在不改变本地 OpenAI/vLLM 协议的前提下设置：

```dotenv
MCE_BEHAVIOR_PROFILE=paper_compatible
MCE_MAX_VALIDATION_ATTEMPTS=3
MCE_AGENT_FINAL_FALLBACK=0
MCE_FORCE_WRITE_ONLY=0
MCE_METRICS_INCLUDE_ERRORS=0
```

该模式会开放 `Edit`、`Grep` 和 `Bash` 给 Meta/Base-Agent，移除集群模式的 Write-only 与短技能提示约束，并使用 success-only 指标分母。它仍然使用本地 `ToolAgent`，不需要 Claude SDK 或协议网关。建议将 `MCE_BEHAVIOR_PROFILE`、四个显式开关与实验配置一起记录到运行 manifest；不要在同一组对照实验中混用两个 profile。

也可使用专用启动脚本，一次选择上述默认值；显式导出的环境变量仍可覆盖开关：

```bash
nohup bash scripts/run_paper_compatible.sh > mce_paper_smoke.log 2>&1 &
```

新评估结果同时保存 `metrics_all_samples`、`metrics_success_only` 和
`execution_error_rate`，`metrics` 仍按选定配置提供主结果。模型调用或上下文
函数内部已经被环境捕获的错误，其原有评分逻辑保留；success-only 分母仅排除
`batch_evaluate` 捕获的执行异常，与上游口径一致。

这是对公开仓库行为的近似兼容，不是 Claude Code 运行时的完整复刻：仍保留
本地上下文裁剪、单工具调用、SKILL 内容注入和运行冒烟校验；不实现
Claude 控制类工具及子 Agent。诊断评估保持知识目录只读，paper 模式仅增加
`Grep`，避免并发样本之间修改共同知识而污染测试。Meta/Base 的 Bash
仍只是设置工作目录，并非操作系统级路径沙箱；Read/Write/Edit/Grep 则
执行显式路径检查。生成代码调用 Bash 时不能假设工作目录等于权限隔离。

回溯点：`pre-paper-compatible-20261002`（提交 `4687fc8`）。
