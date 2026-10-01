#!/usr/bin/env bash
set -euo pipefail

# Export before Python loads .env, so an existing cluster configuration cannot
# silently replace the selected profile's defaults. Explicit exports win.
export MCE_BEHAVIOR_PROFILE=paper_compatible
export MCE_MAX_VALIDATION_ATTEMPTS="${MCE_MAX_VALIDATION_ATTEMPTS:-3}"
export MCE_AGENT_FINAL_FALLBACK="${MCE_AGENT_FINAL_FALLBACK:-0}"
export MCE_FORCE_WRITE_ONLY="${MCE_FORCE_WRITE_ONLY:-0}"
export MCE_METRICS_INCLUDE_ERRORS="${MCE_METRICS_INCLUDE_ERRORS:-0}"

task_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$task_script_dir/run_cluster_smoke.sh" "$@"
