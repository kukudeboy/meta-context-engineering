"""
Lightweight tool-calling agent over the OpenAI Chat Completions protocol.

Replaces claude-agent-sdk so that meta-agent, base-agent and agentic eval
environments can run against any OpenAI-compatible endpoint (vLLM, TGI,
DashScope compatible-mode, ...), including small local models such as
Llama-3.1-8B-Instruct.

Design notes for small models:
- Only a handful of tools (Read / Write / Glob / optional Bash).
- Tool calls emitted as plain-text JSON (common with Llama 3.1) are parsed
  as a fallback when the server returns no structured `tool_calls`.
- Conversation history is trimmed to fit the model's context window.
- Sandbox violations are returned to the model as tool errors instead of
  aborting the run.

Usage mirrors the old ClaudeSDKClient pattern:

    agent = ToolAgent(cwd=iter_dir, sandbox=sandbox, tools=["Read", "Write"])
    result = await agent.query(prompt)      # runs until the model stops calling tools
    result = await agent.query(feedback)    # continues the same conversation
"""

import os
import ast
import json
import uuid
import asyncio
import fnmatch
import logging
import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI, APIStatusError, BadRequestError
from dotenv import load_dotenv

load_dotenv(override=False)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

@dataclass
class Sandbox:
    """
    Path restrictions for file tools.

    Args:
        cwd: Directory that relative paths are resolved against
        read_roots: Directories the agent may read from
        write_roots: Directories the agent may write to
        write_deny: Directories inside write_roots that are still read-only
    """
    cwd: Path
    read_roots: List[Path]
    write_roots: List[Path] = field(default_factory=list)
    write_deny: List[Path] = field(default_factory=list)

    def __post_init__(self):
        self.cwd = Path(self.cwd).resolve()
        self.read_roots = [Path(p).resolve() for p in self.read_roots]
        self.write_roots = [Path(p).resolve() for p in self.write_roots]
        self.write_deny = [Path(p).resolve() for p in self.write_deny]

    def resolve(self, file_path: str) -> Path:
        path = Path(os.path.expanduser(str(file_path)))
        if not path.is_absolute():
            path = self.cwd / path
        return path.resolve()

    @staticmethod
    def _within(path: Path, roots: List[Path]) -> bool:
        return any(path == root or root in path.parents for root in roots)

    def check_read(self, path: Path) -> Optional[str]:
        if not self._within(path, self.read_roots):
            return f"Access denied: reading is restricted to {', '.join(map(str, self.read_roots))}"
        return None

    def check_write(self, path: Path) -> Optional[str]:
        if not self._within(path, self.write_roots):
            return f"Access denied: writing is restricted to {', '.join(map(str, self.write_roots))}"
        if self._within(path, self.write_deny):
            return f"Access denied: cannot write to {', '.join(map(str, self.write_deny))}"
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "Read": {
        "description": "Read a text file. Returns numbered lines. Use offset/limit to page through large files.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path, or path relative to the working directory"},
                "offset": {"type": "integer", "description": "Line number to start from (1-based). Default 1"},
                "limit": {"type": "integer", "description": "Maximum number of lines to return"},
            },
            "required": ["file_path"],
        },
    },
    "Write": {
        "description": "Create or overwrite a file with the COMPLETE content. Parent directories are created automatically.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path, or path relative to the working directory"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["file_path", "content"],
        },
    },
    "Glob": {
        "description": "List files matching a glob pattern (e.g. '**/*.md').",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py' or '**/*.md'"},
                "path": {"type": "string", "description": "Directory to search in. Default: working directory"},
            },
            "required": ["pattern"],
        },
    },
    "Edit": {
        "description": "Replace text in a file inside the sandbox. Read the file first and provide an exact old_string.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path, or path relative to the working directory"},
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence; default false"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    "Grep": {
        "description": "Search text in files inside the sandbox and return matching file, line, and content.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Plain text or regular expression"},
                "path": {"type": "string", "description": "File or directory to search; defaults to working directory"},
                "include": {"type": "string", "description": "Optional glob filter such as '*.py'"},
            },
            "required": ["pattern"],
        },
    },
    "Bash": {
        "description": "Run a shell command in the working directory and return stdout/stderr. Use for simple commands and running Python scripts.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 120, max 600)"},
            },
            "required": ["command"],
        },
    },
}

# Accept common argument aliases produced by small models
_ARG_ALIASES = {
    "path": "file_path",
    "filepath": "file_path",
    "filename": "file_path",
    "file": "file_path",
    "text": "content",
    "cmd": "command",
}


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 200]
    return f"{head}\n\n... [truncated {len(text) - len(head)} chars; output limit is {max_chars} chars] ..."


class ToolExecutor:
    """Executes tool calls inside a Sandbox."""

    def __init__(self, sandbox: Sandbox, max_output_chars: int):
        self.sandbox = sandbox
        self.max_output_chars = max_output_chars

    def run(self, name: str, args: Dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns (output, is_error)."""
        handler = getattr(self, f"_tool_{name.lower()}", None)
        if handler is None:
            return f"Error: unknown tool '{name}'", True
        if name != "Glob":
            args = {_ARG_ALIASES.get(k, k): v for k, v in args.items()}
        try:
            output, is_error = handler(**args)
        except TypeError as e:
            return f"Error: invalid arguments for {name}: {e}", True
        except Exception as e:
            return f"Error: {name} failed: {type(e).__name__}: {e}", True
        return _truncate(output, self.max_output_chars), is_error

    def _tool_read(self, file_path: str, offset: int = 1, limit: int = None, **_):
        path = self.sandbox.resolve(file_path)
        denied = self.sandbox.check_read(path)
        if denied:
            return denied, True
        if not path.exists():
            return f"Error: file not found: {path}", True
        if path.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return f"{path} is a directory. Entries:\n" + "\n".join(entries), False

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(int(offset or 1), 1)
        limit = int(limit) if limit else len(lines)
        selected = lines[offset - 1: offset - 1 + limit]

        numbered, size = [], 0
        for i, line in enumerate(selected, start=offset):
            if len(line) > 2000:
                line = line[:2000] + " ...[line truncated]"
            entry = f"{i:6d}\t{line}"
            size += len(entry) + 1
            if size > self.max_output_chars - 300:
                numbered.append(
                    f"... [output limit reached; file has {len(lines)} lines. "
                    f"Call Read again with offset={i} to continue] ..."
                )
                break
            numbered.append(entry)
        if not numbered:
            return f"(no content; file has {len(lines)} lines)", False
        return "\n".join(numbered), False

    def _tool_write(self, file_path: str, content: str, **_):
        path = self.sandbox.resolve(file_path)
        denied = self.sandbox.check_write(path)
        if denied:
            return denied, True
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        msg = f"Wrote {len(content)} chars to {path}"
        # Immediate syntax feedback helps small models fix code early
        if path.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                return f"{msg}\nWARNING: Python syntax error at line {e.lineno}: {e.msg}. Rewrite the file to fix it.", True
        return msg, False

    def _tool_glob(self, pattern: str, path: str = None, **_):
        base = self.sandbox.resolve(path) if path else self.sandbox.cwd
        denied = self.sandbox.check_read(base)
        if denied:
            return denied, True
        if not base.is_dir():
            return f"Error: not a directory: {base}", True
        # Plain names like "*.md" should also match in subdirectories
        matches = sorted(p for p in base.glob(pattern) if p.is_file())
        if not matches and "/" not in pattern and not pattern.startswith("**"):
            matches = sorted(p for p in base.rglob(pattern) if p.is_file())
        matches = [p for p in matches if not self.sandbox.check_read(p.resolve())]
        if not matches:
            return f"No files match '{pattern}' in {base}", False
        shown = [str(p) for p in matches[:200]]
        if len(matches) > 200:
            shown.append(f"... and {len(matches) - 200} more")
        return "\n".join(shown), False

    def _tool_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        **_,
    ):
        path = self.sandbox.resolve(file_path)
        denied = self.sandbox.check_write(path)
        if denied:
            return denied, True
        if not path.exists() or not path.is_file():
            return f"Error: file not found: {path}", True
        if not old_string:
            return "Error: old_string must not be empty", True
        content = path.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)
        if count == 0:
            return "Error: old_string was not found in the file", True
        if count > 1 and not replace_all:
            return f"Error: old_string occurs {count} times; provide a larger unique string or set replace_all=true", True
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(updated, encoding="utf-8")
        if path.suffix == ".py":
            try:
                ast.parse(updated)
            except SyntaxError as e:
                return f"Edited {path}, but Python syntax is invalid at line {e.lineno}: {e.msg}", True
        return f"Edited {path} ({count if replace_all else 1} replacement(s))", False

    def _tool_grep(self, pattern: str, path: str = None, include: str = None, **_):
        base = self.sandbox.resolve(path) if path else self.sandbox.cwd
        denied = self.sandbox.check_read(base)
        if denied:
            return denied, True
        if not base.exists():
            return f"Error: path not found: {base}", True
        files = [base] if base.is_file() else list(base.rglob(include or "*"))
        try:
            matcher = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regular expression: {e}", True
        matches = []
        for file_path in sorted(p for p in files if p.is_file()):
            if self.sandbox.check_read(file_path.resolve()):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if matcher.search(line):
                    matches.append(f"{file_path}:{line_no}:{line}")
                    if len(matches) >= 200:
                        return "\n".join(matches), False
        return ("\n".join(matches) if matches else f"No matches for {pattern!r}"), False

    def _tool_bash(self, command: str, timeout: int = 120, **_):
        timeout = min(max(int(timeout or 120), 1), 600)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.sandbox.cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s", True
        output = proc.stdout
        if proc.stderr:
            output += ("\n[stderr]\n" if output else "[stderr]\n") + proc.stderr
        output = output.strip() or "(no output)"
        if proc.returncode != 0:
            return f"[exit code {proc.returncode}]\n{output}", True
        return output, False


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Outcome of one `ToolAgent.query` call."""
    final_text: str = ""
    texts: List[str] = field(default_factory=list)
    message_count: int = 0
    tool_calls: int = 0
    turns: int = 0
    stopped_reason: str = "completed"


DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that works on a file system through tools.

Working directory: {cwd}

Rules:
- Act by calling tools. Call exactly ONE tool at a time and wait for its result.
- To create or change a file, call Write with the COMPLETE file content.
- Relative paths are resolved against the working directory.
- If a tool returns an error, read it carefully and try a different action.
- When the task is fully done, reply with a short summary and do NOT call any tool."""


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


class ToolAgent:
    """
    Multi-turn tool-calling agent that keeps conversation state across queries.

    Configuration (environment variables, all optional):
        MCE_AGENT_MODEL               Model name (default: MCE_MODEL)
        MCE_AGENT_API_BASE            Endpoint (default: DASHSCOPE_API_BASE)
        MCE_AGENT_API_KEY             API key (default: DASHSCOPE_API_KEY, or "EMPTY")
        MCE_AGENT_MAX_TURNS           Max model calls per query (default 40)
        MCE_AGENT_MAX_TOKENS          Max completion tokens per call (default 4096)
        MCE_AGENT_MAX_CONTEXT_TOKENS  Prompt budget before history trimming (default 24000)
        MCE_AGENT_MAX_TOOL_OUTPUT     Max chars per tool result (default 12000)
        MCE_AGENT_TEMPERATURE         Sampling temperature (default 0.2)
        MCE_AGENT_TIMEOUT             Seconds per model call (default 600)
    """

    def __init__(
        self,
        sandbox: Sandbox,
        tools: List[str],
        logger: Optional[logging.Logger] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_turns: Optional[int] = None,
        max_tokens: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        max_tool_output_chars: Optional[int] = None,
        console_prefix: Optional[str] = None,
    ):
        for name in tools:
            if name not in TOOL_SCHEMAS:
                raise ValueError(f"Unknown tool '{name}'. Available: {list(TOOL_SCHEMAS)}")

        self.sandbox = sandbox
        self.tool_names = list(tools)
        self.logger = logger or logging.getLogger(__name__)
        self.console_prefix = console_prefix

        self.model = model or os.getenv("MCE_AGENT_MODEL") or os.getenv("MCE_MODEL", "qwen3.7-flash")
        self.max_turns = max_turns or _env_int("MCE_AGENT_MAX_TURNS", 40)
        self.max_tokens = max_tokens or _env_int("MCE_AGENT_MAX_TOKENS", 4096)
        self.max_context_tokens = max_context_tokens or _env_int("MCE_AGENT_MAX_CONTEXT_TOKENS", 24000)
        self.temperature = float(os.getenv("MCE_AGENT_TEMPERATURE", "0.2"))
        self.timeout = float(os.getenv("MCE_AGENT_TIMEOUT", "600"))

        self.client = AsyncOpenAI(
            api_key=os.getenv("MCE_AGENT_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "EMPTY",
            base_url=os.getenv("MCE_AGENT_API_BASE") or os.getenv(
                "DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            timeout=self.timeout,
            max_retries=0,
        )
        self.logger.info("Agent model=%s endpoint=%s", self.model, self.client.base_url)
        self.executor = ToolExecutor(
            sandbox,
            max_output_chars=max_tool_output_chars or _env_int("MCE_AGENT_MAX_TOOL_OUTPUT", 12000),
        )
        self.tools = [
            {"type": "function", "function": {"name": name, **TOOL_SCHEMAS[name]}}
            for name in self.tool_names
        ]

        prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": prompt.format(cwd=sandbox.cwd)}
        ]
        # Number of leading messages that are never trimmed (system + first task prompt)
        self._pinned = 1

    # -- public API ---------------------------------------------------------

    async def query(self, prompt: str) -> AgentResult:
        """Send a user message and run the tool loop until the model stops calling tools."""
        self.messages.append({"role": "user", "content": prompt})
        if self._pinned == 1:
            self._pinned = 2

        result = AgentResult()
        last_call_key, repeat_count = None, 0

        for turn in range(self.max_turns):
            result.turns = turn + 1
            message = await self._complete()
            result.message_count += 1

            text = (message.content or "").strip()
            tool_calls = self._extract_tool_calls(message)

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": text})
                self._log_assistant(text, [])
                if text:
                    result.texts.append(text)
                result.final_text = text
                return result

            # One call per turn keeps Llama-3.1 chat templates happy and small models focused
            call = tool_calls[0]
            self.messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [{
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["raw_arguments"]},
                }],
            })
            self._log_assistant(text, [call])
            if text:
                result.texts.append(text)

            if call["args"] is None:
                output, is_error = (
                    f"Error: could not parse tool arguments as a JSON object: {call['raw_arguments'][:500]}",
                    True,
                )
            elif call["name"] not in self.tool_names:
                output, is_error = (
                    f"Error: tool '{call['name']}' is not available. Available tools: {', '.join(self.tool_names)}",
                    True,
                )
            else:
                output, is_error = await asyncio.to_thread(self.executor.run, call["name"], call["args"])

            call_key = (call["name"], call["raw_arguments"])
            repeat_count = repeat_count + 1 if call_key == last_call_key else 1
            last_call_key = call_key
            if repeat_count >= 3:
                output += (
                    "\n\nNOTE: You have made this exact same call several times in a row. "
                    "Do something different: move on to the next step or finish."
                )

            result.tool_calls += 1
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
            self._log_tool_result(call["name"], output, is_error)

        result.stopped_reason = "max_turns"
        self.logger.warning(f"Agent stopped after reaching max_turns={self.max_turns}")
        return result

    # -- model call ---------------------------------------------------------

    async def answer_without_tools(self, prompt: str) -> str:
        """Ask for a final plain-text answer with tools disabled (e.g. after hitting max_turns)."""
        self.messages.append({"role": "user", "content": prompt})
        message = await self._complete(use_tools=False)
        text = (message.content or "").strip()
        self.messages.append({"role": "assistant", "content": text})
        self._log_assistant(text, [])
        return text

    async def _complete(self, use_tools: bool = True):
        """Call the chat completions endpoint with retries and context-overflow handling."""
        budget = self.max_context_tokens
        last_error = None
        kwargs = {}
        if use_tools and self.tools:
            kwargs = {"tools": self.tools, "tool_choice": "auto"}
        for attempt in range(4):
            self._trim_history(budget)
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    **kwargs,
                )
                return response.choices[0].message
            except BadRequestError as e:
                last_error = e
                if "context" in str(e).lower() or "token" in str(e).lower():
                    budget = int(budget * 0.6)
                    self.logger.warning(f"Context overflow, trimming history to ~{budget} tokens: {e}")
                    continue
                raise
            except APIStatusError as e:
                # Auth / not-found / permission errors will not fix themselves
                if 400 <= e.status_code < 500 and e.status_code not in (408, 409, 429):
                    raise
                last_error = e
                self.logger.warning(f"Model call failed (attempt {attempt + 1}/4): {type(e).__name__}: {e}")
                await asyncio.sleep(5 * (attempt + 1))
            except Exception as e:
                last_error = e
                self.logger.warning(f"Model call failed (attempt {attempt + 1}/4): {type(e).__name__}: {e}")
                await asyncio.sleep(5 * (attempt + 1))
        raise RuntimeError(
            f"Model call failed after retries (model={self.model}, endpoint={self.client.base_url}): {last_error}"
        ) from last_error

    # -- tool call parsing --------------------------------------------------

    def _extract_tool_calls(self, message) -> List[Dict[str, Any]]:
        """Return structured tool calls, falling back to JSON embedded in text content."""
        calls = []
        for tc in message.tool_calls or []:
            raw = tc.function.arguments or "{}"
            calls.append({
                "id": tc.id or f"call_{uuid.uuid4().hex[:12]}",
                "name": tc.function.name,
                "raw_arguments": raw,
                "args": self._parse_args(raw),
            })
        if calls:
            return calls

        parsed = self._parse_text_tool_call(message.content or "")
        if parsed:
            name, args = parsed
            raw = json.dumps(args, ensure_ascii=False)
            return [{"id": f"call_{uuid.uuid4().hex[:12]}", "name": name, "raw_arguments": raw, "args": args}]
        return []

    @staticmethod
    def _parse_args(raw: str) -> Optional[Dict[str, Any]]:
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return None
        # Some models double-encode the arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None
        return args if isinstance(args, dict) else None

    def _parse_text_tool_call(self, content: str) -> Optional[tuple[str, Dict[str, Any]]]:
        """Parse Llama-style text tool calls: {"name": "Write", "parameters": {...}}."""
        text = content.strip().replace("<|python_tag|>", "").replace("<|eom_id|>", "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        start = text.find("{")
        if start == -1:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        if isinstance(obj.get("function"), dict):
            obj = obj["function"]
        name = obj.get("name")
        args = obj.get("parameters", obj.get("arguments", {}))
        if isinstance(args, str):
            args = self._parse_args(args)
        if name in self.tool_names and isinstance(args, dict):
            return name, args
        return None

    # -- history management -------------------------------------------------

    @staticmethod
    def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
        # Conservative estimate (~3 chars per token for mixed code/English/Chinese)
        return len(json.dumps(messages, ensure_ascii=False)) // 3

    def _trim_history(self, budget: int) -> None:
        """Elide old tool outputs, then drop oldest turns, until the prompt fits the budget."""
        budget -= self._estimate_tokens(self.tools)
        if self._estimate_tokens(self.messages) <= budget:
            return

        # Pass 1: elide old tool outputs and long Write contents (keep the most recent 4 messages intact)
        cutoff = len(self.messages) - 4
        for i in range(self._pinned, cutoff):
            msg = self.messages[i]
            if msg["role"] == "tool" and len(msg["content"]) > 300:
                msg["content"] = msg["content"][:200] + "\n... [old tool output elided to save context] ..."
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                fn = msg["tool_calls"][0]["function"]
                if len(fn["arguments"]) > 600:
                    args = self._parse_args(fn["arguments"]) or {}
                    if "content" in args:
                        args["content"] = "[content elided to save context]"
                        fn["arguments"] = json.dumps(args, ensure_ascii=False)
        if self._estimate_tokens(self.messages) <= budget:
            return

        # Pass 2: drop whole turns (assistant + its tool result) after the pinned prefix
        while self._estimate_tokens(self.messages) > budget and len(self.messages) > self._pinned + 2:
            end = self._pinned + 1
            while end < len(self.messages) and self.messages[end]["role"] == "tool":
                end += 1
            del self.messages[self._pinned:end]
        self.logger.info(f"Trimmed conversation history to {len(self.messages)} messages")

    # -- logging ------------------------------------------------------------

    def _log_assistant(self, text: str, calls: List[Dict[str, Any]]) -> None:
        parts = ["\n" + "=" * 80, "🤖 ASSISTANT", "=" * 80]
        if text:
            parts.append(text)
        for call in calls:
            parts.append(f"🔧 {call['name']}  {call['raw_arguments']}")
            if self.console_prefix:
                target = (call["args"] or {}).get("file_path") or (call["args"] or {}).get("command") \
                    or (call["args"] or {}).get("pattern") or ""
                print(f"{self.console_prefix} {call['name']} {str(target)[:100]}", flush=True)
        self.logger.info("\n".join(parts))

    def _log_tool_result(self, name: str, output: str, is_error: bool) -> None:
        status = "✗" if is_error else "✓"
        self.logger.info(f"\n🔧 Result [{name}] {status}\n{_truncate(output, 4000)}")
