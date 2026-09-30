#!/bin/bash

uv run python -m mce.main \
    --workspace "workspace/symptom_diagnosis_agent" \
    --env "symptom_diagnosis_agent" \
    --train-data "env/symptom_diagnosis/data/train.jsonl" \
    --val-data "env/symptom_diagnosis/data/val.jsonl" \
    --model "${MCE_MODEL:-qwen3.7-flash}" \
    --iterations 2 \
    --train-limit 50 \
    --val-limit 20 \
    --train-batch-size 25 \
    --log-dir "logs/symptom_diagnosis_agent"

