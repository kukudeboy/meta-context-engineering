#!/usr/bin/env bash
set -euo pipefail

# Use the existing cluster environment; never run uv/pip/conda installation here.
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
task_python="${MCE_CLUSTER_PYTHON:-$HOME/liyahui/miniconda/my_vllm_env/bin/python}"
if [[ ! -x "$task_python" ]]; then
    printf 'Python interpreter not found: %s\nSet MCE_CLUSTER_PYTHON to an existing Python 3.11+ interpreter.\n' "$task_python" >&2
    exit 1
fi
cd -- "$task_project_root"

# main.py loads .env without overriding exported variables and selects MCE_MODEL.
# Extra CLI arguments override these smoke-run defaults.
task_workspace="${MCE_CLUSTER_WORKSPACE:-workspace/symptom_diagnosis_cluster_smoke_$(date +%Y%m%d_%H%M%S)_${BASHPID}}"
exec "$task_python" -u -m mce.main \
    --workspace "$task_workspace" \
    --env symptom_diagnosis \
    --train-data env/symptom_diagnosis/data/train.jsonl \
    --val-data env/symptom_diagnosis/data/val.jsonl \
    --iterations 1 \
    --train-limit 5 \
    --train-batch-size 5 \
    --val-limit 5 \
    --log-dir logs/symptom_diagnosis_cluster \
    "$@"
