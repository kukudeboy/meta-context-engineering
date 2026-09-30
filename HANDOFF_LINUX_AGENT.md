# 交接文档：Llama-3.1-8B-Instruct 离线集群部署（Windows 侧 → Linux 侧 Agent）

> 交接时间：2026-09-30
> 交接人：李亚辉（Windows 侧，与 WorkBuddy 协作完成了全部方案设计与 Windows 侧准备）
> 接收方：Linux 侧 Agent（工作环境：VMware Ubuntu 22.04 虚拟机 + NSCC-GZ 集群 SSH）
> 当前状态：**模型权重正在从 Windows 向集群传输中**，其余步骤待你接手执行。

---

## 1. 任务目标

把 `Meta-Llama-3.1-8B-Instruct`（bf16 全量，~14 GB）部署到**离线**的超算集群上，用 vLLM 起一个 OpenAI 兼容的推理 API 服务。

## 2. 集群实测信息（已验证，勿重复探测）

| 项目 | 值 |
|---|---|
| 中心 | 国家超级计算广州中心（NSCC-GZ） |
| 登录命令 | `ssh -J 16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022 nsccgz_ywang_ywj@dl2a80093wm` |
| 集群别名 | Windows 侧已配 `C:\Users\29151\.ssh\config`，`ssh nscc` 等价于上面整条命令 |
| GPU | 2× NVIDIA A800 80GB PCIe（sm_80），单卡跑 bf16 全量绰绰有余 |
| 驱动 | 535.104.12（最高支持 CUDA 12.2；nvcc 12.1） |
| 系统 | Ubuntu 22.04，GLIBC 2.35 |
| tmux | 3.3a，可用 |
| conda | /app/common/anaconda3/5.4.0，base Python 3.8（**太老，不要直接用**） |
| 网络 | **集群不能访问外网**；但跳板机 SSH 可用，scp/rsync 可从外部推文件 |
| 模型目标路径 | `/XYFS01/nsccgz_ywang_ywj/liyahui/model/`（大容量数据盘，无配额之忧） |

## 3. 方案（已定稿，勿更改技术选型）

**vLLM 0.8.5 + Python 3.11 + conda-pack 整包迁移**。理由：

- 驱动 535 偏旧，**最新 vLLM 轮子（CUDA 12.6/12.8 编译）有兼容风险**，0.8.5（torch 2.6，CUDA 12.1 系）在 535 上稳定；
- 集群 conda 太老且离线无法建新 Python 环境 → 必须在能联网的 Linux（VMware Ubuntu 22.04 虚拟机）里配好环境，`conda-pack` 打包整份带走；
- 虚拟机 Ubuntu 22.04（glibc 2.35）与集群完全对齐，无 GLIBC 兼容问题。

## 4. 当前进度

| 步骤 | 状态 |
|---|---|
| 本地模型下载（modelscope → `D:\models\Meta-Llama-3.1-8B-Instruct`） | ✅ 完成，18 文件 29.93 GB，已校验完整 |
| Windows `~/.ssh/config` 别名 `nscc` | ✅ 已配置并可用 |
| 集群环境探测（GPU/系统/tmux/conda） | ✅ 完成 |
| 部署清单 `deploy_checklist.md` | ✅ 已写好（Windows 侧工作区） |
| **模型权重上传集群**（Windows scp，带 ProxyJump） | 🔄 **正在进行** |
| VMware VM 内配 vLLM 环境 + conda-pack 打包 | ⬜ 待接手 |
| 环境包 + 用户代码上传集群 | ⬜ 待接手 |
| 集群解压环境、启动 vllm serve、验证 | ⬜ 待接手 |

## 5. 你（Linux Agent）要做的完整步骤

### 阶段 A：VMware Ubuntu 22.04 虚拟机里配环境（虚拟机可联网）

```bash
# 预检
ldd --version    # 应为 2.35；uname -m 应为 x86_64；df -h ~ 剩余 ≥ 15 GB

# 装 Miniconda（若虚拟机还没有）
wget https://mirrors.aliyun.com/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p ~/miniconda3
source ~/miniconda3/bin/activate

# 建环境、装 vLLM（源用阿里云）
conda create -n vllm python=3.11 -y
conda activate vllm
pip install vllm==0.8.5 -i https://mirrors.aliyun.com/pypi/simple
pip install conda-pack -i https://mirrors.aliyun.com/pypi/simple
python -c "import vllm; print(vllm.__version__)"   # 必须输出 0.8.5

# 打包
conda pack -n vllm -o ~/vllm_env.tar.gz   # 产出 4~6 GB
```

### 阶段 B：上传环境包和代码到集群

```bash
# 跳板机参数必须带！rsync 支持断点续传
rsync -avP -e "ssh -J 16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022" \
  ~/vllm_env.tar.gz nsccgz_ywang_ywj@dl2a80093wm:/XYFS01/nsccgz_ywang_ywj/liyahui/

# 用户自己的推理代码（如有）
rsync -avP -e "ssh -J 16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022" \
  ~/my_llama_code/ nsccgz_ywang_ywj@dl2a80093wm:/XYFS01/nsccgz_ywang_ywj/liyahui/
```

### 阶段 C：集群上解压 + 启动（SSH 登录集群后）

```bash
mkdir -p ~/envs/vllm
tar -xzf /XYFS01/nsccgz_ywang_ywj/liyahui/vllm_env.tar.gz -C ~/envs/vllm
source ~/envs/vllm/bin/activate
# 若 CondaPack 提示 fixup，按提示执行 conda-unpack
python -c "import vllm; print(vllm.__version__)"   # 验证 0.8.5

# tmux 里起服务（模型路径以实际传输完成后的位置为准）
tmux new -s llama
vllm serve /XYFS01/nsccgz_ywang_ywj/liyahui/model/Meta-Llama-3.1-8B-Instruct \
  --served-model-name llama31-8b \
  --host 0.0.0.0 --port 8000 \
  --dtype bfloat16 --max-model-len 8192 --gpu-memory-utilization 0.90
# 日志出现 "Uvicorn running on http://0.0.0.0:8000" 即成功
```

### 阶段 D：验证

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "llama31-8b", "messages": [{"role": "user", "content": "你好"}]}'
# choices[0].message.content 有内容 = 部署成功
```

外部访问走 SSH 隧道：`ssh -J 16c27855d1cc43ccb2ea6bdd9cb9fec7@proxy.nscc-gz.cn:8022 -L 8000:localhost:8000 nsccgz_ywang_ywj@dl2a80093wm`

## 6. 红线与注意事项（务必遵守）

1. **vLLM 版本锁死 0.8.5**，不要升级——新版本 CUDA 轮子与集群 535 驱动有兼容风险；
2. **不要在集群终端里敲 scp/rsync**（方向反了），传输命令一律在本地/虚拟机敲；
3. **所有 scp/rsync 必须带 ProxyJump 跳板参数**，否则连不上集群；
4. scp **无断点续传**，大文件传输用 `rsync -avP`；
5. 模型 `original/` 子目录（16 GB，Meta 原始 .pth 权重）**vLLM 用不到**；如发现被一并传上集群，可向用户确认后删除集群侧该子目录（本地 D 盘的是否删除需用户确认，不要擅动）；
6. 集群 conda base（py3.8）**不要动**，全部用打包来的环境（`source activate`，不是 `conda activate`）；
7. 服务必须放 **tmux**（或 nohup）里跑，SSH 断开不能死；
8. 集群环境离线：**任何 pip/conda install 在集群上都会失败**，一切依赖必须在虚拟机里装好再打包。

## 7. 常见故障速查

| 现象 | 解法 |
|---|---|
| `GLIBC_2.3x not found` | 打包环境 glibc 高于集群——虚拟机必须用 Ubuntu 22.04 |
| vLLM 报 CUDA 不支持 / sm_80 不在列表 | 版本装错，重装 `vllm==0.8.5` |
| ssh 卡住/Connection refused | 检查是否漏了 `-J` 跳板参数 |
| 传输中断 | rsync 重跑同一命令续传；scp 只能重来 |
| 显存 OOM（基本不会） | 调小 `--max-model-len` 或 `--gpu-memory-utilization` |
| 集群要 Slurm 提交 | 用 sbatch 脚本（见 deploy_checklist.md 附录），gres=gpu:1 即可 |

## 8. 相关文件位置

- 本文件与 `deploy_checklist.md`：Windows 工作区 `C:\Users\29151\WorkBuddy\2026-09-30-14-45-39\`
- 本地模型：`D:\models\Meta-Llama-3.1-8B-Instruct\`
- 集群模型目标：`/XYFS01/nsccgz_ywang_ywj/liyahui/model/Meta-Llama-3.1-8B-Instruct/`
- 集群环境计划位置：`~/envs/vllm/`（家目录下，环境包在 `/XYFS01/nsccgz_ywang_ywj/liyahui/vllm_env.tar.gz`）
