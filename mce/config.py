"""Runtime behavior profiles used by the cluster and paper-compatible modes."""

import os

from dotenv import load_dotenv

load_dotenv(override=False)


def behavior_profile() -> str:
    profile = os.getenv("MCE_BEHAVIOR_PROFILE", "cluster_safe").strip().lower()
    if profile not in {"cluster_safe", "paper", "paper_compatible", "original"}:
        raise ValueError(f"Unknown MCE_BEHAVIOR_PROFILE: {profile!r}")
    return profile


def is_paper_compatible() -> bool:
    return behavior_profile() in {"paper", "paper_compatible", "original"}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value not in {"0", "false", "no", "off", "1", "true", "yes", "on"}:
        raise ValueError(f"Invalid boolean value for {name}: {value!r}")
    return value in {"1", "true", "yes", "on"}


def validation_attempts() -> int:
    default = 3 if is_paper_compatible() else 5
    attempts = int(os.getenv("MCE_MAX_VALIDATION_ATTEMPTS", str(default)))
    if attempts < 1:
        raise ValueError("MCE_MAX_VALIDATION_ATTEMPTS must be at least 1")
    return attempts


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
        tools = [name.strip() for name in configured.split(",") if name.strip()]
        if role == "eval":
            # Evaluation must not modify the context shared by concurrent samples.
            tools = [name for name in tools if name in {"Read", "Glob", "Grep"}]
        return tools

    if role == "eval":
        if is_paper_compatible():
            return ["Read", "Glob", "Grep"]
        return ["Read", "Glob"]

    if is_paper_compatible():
        tools = ["Read", "Write", "Edit", "Glob", "Grep"]
        if force_write_only():
            tools.remove("Edit")
        if env_bool("MCE_AGENT_ENABLE_BASH", True):
            tools.append("Bash")
        return tools

    tools = ["Read", "Write", "Glob"]
    if role == "base" and env_bool("MCE_AGENT_ENABLE_BASH", True):
        tools.append("Bash")
    return tools
