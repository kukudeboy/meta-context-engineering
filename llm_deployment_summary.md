# 广州超算集群大模型部署与调用技术备忘录

本技术备忘录详细记录了在广州超算断网计算节点（双卡 NVIDIA A800 80GB）上成功部署 **Meta-Llama-3.1-8B-Instruct** 的全流程环境配置、离线修复补丁、服务启停运维命令以及标准的 OpenAI API 通信格式与调用指南。可作为当前环境的操作手册，也可直接作为上下文信息传递给下游 Agent 或开发者。

---

## 一、 部署基础设施与环境拓扑

### 1. 硬件与网络环境
- **集群节点**: `dl2a80093wm-pfcq5`（SSH 配置别名：`gz-node`）
- **跳板机**: `16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022`
- **加速硬件**: 2 × NVIDIA A800 80GB PCIe（总显存 160 GB，空闲驱动 CUDA 12.2 / Driver 535.104.12）
- **网络条件**: 计算节点为**纯内网断网环境**（无法直接访问公网 PyPI/Hugging Face）

### 2. 存储与环境路径
- **集群主目录**: `~/`（对应 `/HOME/nsccgz_ywang/nsccgz_ywang_ywj/`）
- **Conda 虚拟环境路径**: `~/liyahui/miniconda/my_vllm_env`
- **模型文件仓库**: `~/liyahui/model/`
  - 对话大模型: `~/liyahui/model/Meta-Llama-3.1-8B-Instruct`
  - 向量模型: `~/liyahui/model/Qwen3-Embedding-0.6B`
- **离线 Wheel 补丁包**: `~/liyahui/miniconda/vllm_patch_wheels/`

### 3. 关键依赖兼容性与离线修复记录
- **推理引擎**: `vLLM 0.8.5`
- **底层依赖**: `PyTorch 2.6.0+cu124` + `CUDA 12.2`
- **关键避坑修复**: 原环境中的 `transformers 5.17.0` 移除了 `all_special_tokens_extended` 属性，导致 vLLM 分词器初始化崩溃。已通过纯离线方式降级覆盖为：
  - `transformers==4.57.6`
  - `tokenizers==0.22.2`
  - `huggingface-hub==0.36.2`

---

## 二、 服务启停与运维指令集

### 1. 激活 Conda 环境
在集群终端执行：
```bash
source ~/miniconda/my_vllm_env/bin/activate
# 或者
conda activate ~/liyahui/miniconda/my_vllm_env
```

### 2. 模型启动方案

#### 方案 A：双卡张量并行（只跑 LLaMA 8B，极致吞吐与超长 64K 上下文）
```bash
nohup python -m vllm.entrypoints.openai.api_server \
    --model ~/liyahui/model/Meta-Llama-3.1-8B-Instruct \
    --served-model-name llama-3.1-8b \
    --tensor-parallel-size 2 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 65536 \
    --trust-remote-code \
    > ~/liyahui/model/vllm_llama.log 2>&1 &
```

#### 方案 B：一张卡开一个模型（GPU 0 跑对话大模型，GPU 1 跑向量嵌入模型）

**1. 在 GPU 0 上启动 LLaMA 3.1 8B（端口 8000）**
```bash
CUDA_VISIBLE_DEVICES=0 nohup python -m vllm.entrypoints.openai.api_server \
    --model ~/liyahui/model/Meta-Llama-3.1-8B-Instruct \
    --served-model-name llama-3.1-8b \
    --port 8000 \
    --gpu-memory-utilization 0.85 \
    --max-model-len 32768 \
    --trust-remote-code \
    > ~/liyahui/model/vllm_llama.log 2>&1 &
```

**2. 在 GPU 1 上启动 Qwen3-Embedding-0.6B（端口 8001）**
```bash
CUDA_VISIBLE_DEVICES=1 nohup python -m vllm.entrypoints.openai.api_server \
    --model ~/liyahui/model/Qwen3-Embedding-0.6B \
    --served-model-name qwen-embed \
    --port 8001 \
    --gpu-memory-utilization 0.3 \
    --trust-remote-code \
    > ~/liyahui/model/vllm_embed.log 2>&1 &
```

---

### 3. 查看实时启动日志
```bash
# 查看 LLaMA 日志
tail -f ~/liyahui/model/vllm_llama.log

# 查看 Embedding 模型日志
tail -f ~/liyahui/model/vllm_embed.log
```

### 4. 显存状态监控
```bash
nvidia-smi
```

### 5. 一键停止服务（释放显存）
```bash
# 停止所有 vLLM 实例
pkill -f "vllm.entrypoints.openai.api_server"

# 或者根据端口精准终止指定服务：
# lsof -ti:8000 | xargs kill -9   # 仅停 8000
# lsof -ti:8001 | xargs kill -9   # 仅停 8001
```

---

## 三、 本地连接穿透（SSH 端口转发）

如果同时启动了两个模型（8000 和 8001 端口），本地电脑一次性映射两个端口：

```bash
# 方式 1：利用本地 ~/.ssh/config 中预配置的别名
ssh -N -L 8000:localhost:8000 -L 8001:localhost:8001 gz-node

# 方式 2：完整跳板机命令
ssh -N -L 8000:localhost:8000 -L 8001:localhost:8001 \
    -J 16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022 \
    nsccgz_ywang_ywj@dl2a80093wm-pfcq5
```

---

## 四、 API 接口通信格式与调用规范

vLLM 服务完全遵循 **OpenAI 兼容协议**。

### 1. 核心接口列表

| 模型服务 | 接口分类 | 路径 | 完整请求地址 | 作用 |
| :--- | :--- | :--- | :--- | :--- |
| **LLaMA 对话** | 对话补全 | `/v1/chat/completions` | `http://localhost:8000/v1/chat/completions` | 多轮问答与代码生成 |
| **LLaMA 对话** | 文本补全 | `/v1/completions` | `http://localhost:8000/v1/completions` | 纯文本续写补全 |
| **Qwen 向量** | 向量嵌入 | `/v1/embeddings` | `http://localhost:8001/v1/embeddings` | 文本转稠密向量（RAG） |
| 通用 | 模型列表 | `/v1/models` | `http://localhost:8000/v1/models` | 查询可用模型信息 |

---

### 2. 对话补全请求格式 (`POST /v1/chat/completions`)

#### 请求 Body (JSON Schema)
```json
{
  "model": "llama-3.1-8b",
  "messages": [
    {
      "role": "system",
      "content": "你是由 DeepSeek/Google 专家优化的 AI 编程助手。"
    },
    {
      "role": "user",
      "content": "请用 Python 写一个支持泛型的 LRU 缓存类。"
    }
  ],
  "temperature": 0.7,
  "top_p": 0.9,
  "max_tokens": 2048,
  "stream": false
}
```

#### 常见参数定义
- `model` (string, 必须): 模型别名，填 `llama-3.1-8b`。
- `messages` (list, 必须): 历史上下文消息列表，每个元素包含 `role` (`system`/`user`/`assistant`/`tool`) 与 `content`。
- `temperature` (float, 可选): 采样温度，0~2 之间。越低回答越严谨，推荐代码任务设为 `0.2`，开放问答设为 `0.7`。
- `top_p` (float, 可选): 核采样概率阈值，通常与 `temperature` 二选一微调，推荐 `0.8~0.95`。
- `max_tokens` (int, 可选): 允许生成的最大 token 数量。
- `stream` (bool, 可选): 是否开启流式传输（SSE 逐步打字机吐字），默认 `false`。
- `stop` (list[string], 可选): 遇到指定文本串立即停止生成。

---

### 3. 响应结果格式示例 (JSON)

```json
{
  "id": "chatcmpl-4c22b84dca3142989bedf4eab64e0954",
  "object": "chat.completion",
  "created": 1790836273,
  "model": "llama-3.1-8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "reasoning_content": null,
        "content": "你好，我是一个大型语言模型...",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "completion_tokens": 56,
    "total_tokens": 101
  }
}
```

---

## 五、 代码调用示例

### 1. cURL 终端命令行调用

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.1-8b",
    "messages": [
      {"role": "user", "content": "请介绍一下自己的技术架构与主要特性。"}
    ],
    "temperature": 0.7,
    "max_tokens": 256
  }'
```

### 2. Python SDK 标准调用（非流式）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="none",  # vLLM 无需实际密钥，传占位字符串即可
)

response = client.chat.completions.create(
    model="llama-3.1-8b",
    messages=[
        {"role": "system", "content": "你是一位计算机体系结构专家。"},
        {"role": "user", "content": "简述 GPU Tensor Core 与 CUDA Core 的本质区别。"}
    ],
    temperature=0.3,
    max_tokens=1024,
)

print(response.choices[0].message.content)
```

### 3. Python SDK 流式输出（打字机效果）

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="none")

stream = client.chat.completions.create(
    model="llama-3.1-8b",
    messages=[{"role": "user", "content": "写一首赞美高性能计算集群的诗。"}],
    stream=True,
    max_tokens=512,
)

for chunk in stream:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
print()
```

---

### 4. Embedding 向量模型调用指令（端口 8001）

#### cURL 终端命令行生成向量：
```bash
curl http://localhost:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-embed",
    "input": ["广州超算中心", "高性能计算与大模型推理"]
  }'
```

#### Python SDK 生成向量：
```python
from openai import OpenAI

# 连接端口 8001 的 Embedding 服务
embed_client = OpenAI(base_url="http://localhost:8001/v1", api_key="none")

response = embed_client.embeddings.create(
    model="qwen-embed",
    input=["这是一段需要存入向量数据库的知识库文本段落。"],
    encoding_format="float",
)

vector = response.data[0].embedding
print(f"生成的向量维度: {len(vector)}")  # 如 1024 / 1536 维
```
