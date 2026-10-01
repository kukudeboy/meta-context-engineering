"""Runtime behavior profiles used by the cluster and paper-compatible modes."""

import os

from dotenv import load_dotenv

load_dotenv(override=False)


def behavior_profile() -> str:
    return os.getenv("MCE_BEHAVIOR_PROFILE", "cluster_safe").strip().lower()


def is_paper_compatible() -> bool:
    return behavior_profile() in {"paper", "paper_compatible", "original"}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def validation_attempts() -> int:
    default = 3 if is_paper_compatible() else 5
    return int(os.getenv("MCE_MAX_VALIDATION_ATTEMPTS", str(default)))


def final_answer_fallback_enabled() -> bool:
    return env_bool("MCE_AGENT_FINAL_FALLBACK", not is_paper_compatible())


def force_write_only() -> bool:
    return env_bool("MCE_FORCE_WRITE_ONLY", not is_paper_compatible())


def include_errors_in_metrics() -> bool:
    return env_bool("MCE_METRICS_INCLUDE_ERRORS", not is_paper_compatible())


def agent_tools(role: str = "base") -> list[str]:
    """Return tools for a role while keeping an explicit override for experiments."""
    configured = os.getenv("MCE_AGENT_TOOLS")
    if configured:
        return [name.strip() for name in configured.split(",") if name.strip()]

    if role == "eval":
        if is_paper_compatible():
            return ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
        return ["Read", "Glob"]

    if is_paper_compatible():
        return ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

    tools = ["Read", "Write", "Glob"]
    if env_bool("MCE_AGENT_ENABLE_BASH", True):
        tools.append("Bash")
    return tools
