# 本地 ToolAgent 的 paper-compatible 行为模式

## 回溯与范围

改造前快照已提交并推送至 `origin/main`：

- 提交：`4687fc8`
- Tag：`pre-paper-compatible-20261002`

`.env` 与虚拟环境继续保持 Git 忽略；上述快照包括源码和部署配置模板，不包含本地密钥。

兼容对象是官方公开仓库 `metaevo-ai/meta-context-engineering` 的
`c4b7a7c` 快照。目标是恢复公开代码的提示词和执行设置，保留本地
OpenAI 协议，不恢复 Claude SDK，也不承诺复现原论文模型的绝对性能。

## 启用方式

默认仍是 `cluster_safe`。推荐在集群项目根目录运行：

```bash
nohup bash scripts/run_paper_compatible.sh > mce_paper_smoke.log 2>&1 &
```

该脚本先导出兼容模式及开关，再调用已有离线冒烟入口；默认 5 条训练、
5 条验证、1 次迭代、新建独立 workspace。无需 uv 或在线安装。
解释器默认是 `$HOME/liyahui/miniconda/my_vllm_env/bin/python`，
也可设置 `MCE_CLUSTER_PYTHON` 指定准备好的客户端解释器。

可追加原有 CLI 参数，例如：

```bash
bash scripts/run_paper_compatible.sh \
  --workspace workspace/symptom_diagnosis_paper_run1 \
  --train-limit 50 --train-batch-size 25 --val-limit 20 --iterations 3
```

也可直接设置 `.env`：

```dotenv
MCE_BEHAVIOR_PROFILE=paper_compatible
MCE_MAX_VALIDATION_ATTEMPTS=3
MCE_AGENT_FINAL_FALLBACK=0
MCE_FORCE_WRITE_ONLY=0
MCE_METRICS_INCLUDE_ERRORS=0
```

仅修改 `MCE_BEHAVIOR_PROFILE` 时，现有 `.env` 中显式设置的
`MCE_MAX_VALIDATION_ATTEMPTS=5` 等仍优先于模式默认值。
启动脚本避免这种情况；已在 Shell 显式导出的开关仍会覆盖脚本默认值。

## 两种模式的行为

| 项目 | cluster_safe | paper_compatible |
| --- | --- | --- |
| Meta 工具 | Read / Write / Glob | Read / Write / Edit / Glob / Grep / Bash |
| Base 工具 | Read / Write / Glob / Bash | Read / Write / Edit / Glob / Grep / Bash |
| 诊断 Agent 工具 | Read / Glob | Read / Glob / Grep |
| Meta 技能提示 | 少于 800 词、短句和编号步骤 | 恢复官方提示，不额外限制长度 |
| Base 文件修改提示 | 完整 Write、简单 Bash | 恢复官方提示 |
| 系统文件修改提示 | 完整 Write | 可选择 Write 或 Edit |
| 校验反馈 | 集群严格反馈、完整 Write | 官方格式的接口修复提醒 |
| 默认校验次数 | 5 | 3 |
| 诊断最终回答额外请求 | 开启 | 关闭 |
| 主指标分母 | 全部样本 | 成功评估样本 |

设置 `MCE_AGENT_ENABLE_BASH=0` 可在两种模式中关闭默认 Meta/Base Bash。
显式 `MCE_AGENT_TOOLS` 可覆盖默认工具列表；诊断评估仍过滤为只读工具。
`MCE_FORCE_WRITE_ONLY=1` 会在默认 paper 工具集合中移除 Edit，并恢复
系统/Base/校验提示中的完整 Write 要求。

校验次数、fallback、Write-only 与指标口径可独立覆盖。
配置拼写错误和小于 1 的校验次数会明确报错。

## 统计与审计

`batch_evaluate` 同时保存：

- `summary.metrics_all_samples`：全部样本分母，执行异常按零分计入。
- `summary.metrics_success_only`：排除评估器捕获的执行异常，沿用上游口径。
- `summary.metrics`：由 `MCE_METRICS_INCLUDE_ERRORS` 决定的主指标。
- `summary.execution_error_rate`：评估器捕获的执行异常比例。
- `summary.metric_denominator` 与 `summary.behavior_profile`：说明结果口径。

任务环境内部已经捕获的模型或上下文函数错误仍按原逻辑评分，
success-only 不会重新将这些记录剔除。不要将 success-only 的结果
单独作为部署性能报告；同时查看全部样本指标和错误率。

Meta/Base 日志会记录实际 profile、工具集合和校验次数。

## 工具实现与保留差异

Edit 支持唯一精确文本替换，重复匹配需要显式 `replace_all`。
Edit 和 Grep 都检查允许路径及符号链接解析后的目标，Base 的
`utils/` 写入禁令继续有效。

这不是 Claude Code 工具的完整复刻：不实现 Task、Skill 和计划管理等
控制工具；仍单次执行一个工具调用，以适配 Llama 3.1 模板。
仍保留 SKILL 内容注入、历史消息裁剪、输出限制、运行冒烟校验、
独立 Embedding 服务和错误追踪。

诊断评估刻意保留知识目录只读，避免并发样本修改共享知识。
Meta/Base 的 Bash 设置工作目录，但不提供操作系统级路径隔离；
不能将 `cwd` 视为 Bash 沙箱。Read/Write/Edit/Grep 提供显式路径检查。

原公开 Base 提示中仍包含 `uv run python ...` 的执行建议。兼容模式
为保持官方提示一致而保留它；集群启动入口本身不用 uv。若模型生成的
Bash 使用该命令，集群环境需要已有 uv/环境，或单独调整执行建议并在
实验设置中披露这项离线适配。

## 本地验证

现有 11 项客户端回归保留；新增 12 项行为测试覆盖：

- 模式默认值及显式覆盖；
- Meta/Base 提示与官方本地快照的一致性（该快照存在时）；
- 系统提示和校验反馈；
- Edit 重复匹配、空字符串和读写路径拒绝；
- Grep 外部路径和符号链接过滤；
- 主指标与双分母统计；
- 诊断 fallback 的模式切换。

本地共 23 项测试通过，修改过的 Python 文件语法检查通过。
这些测试使用模拟请求，不验证集群服务可用性或模型实际准确率。
上传后应先运行上述冒烟入口，再使用相同数据和解码设置比较两种模式。
