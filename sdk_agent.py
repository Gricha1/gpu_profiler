"""Local Cursor SDK agent with SSH tools for remote GPU hosts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from cursor_sdk import (
    AsyncClient,
    CustomTool,
    CustomToolContext,
    LocalAgentOptions,
    SDKAssistantMessage,
    SDKStatusMessage,
    SDKThinkingMessage,
    SDKToolUseMessage,
    TextDeltaUpdate,
)

from cursor_projects import is_safe_remote_path

ROOT = Path(__file__).resolve().parent
WORKSPACES = ROOT / "_agent_workspaces"
SSH_TIMEOUT_SEC = 45
MAX_OUTPUT_CHARS = 40_000

_client: AsyncClient | None = None
_client_lock = asyncio.Lock()
_sessions: dict[str, "AgentSession"] = {}
_sessions_lock = asyncio.Lock()


def _api_key() -> str:
    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("CURSOR_API_KEY не задан (положи в .env)")
    return key


def _session_key(host: str, path: str) -> str:
    return f"{host}:{path}"


def _workspace_dir(host: str, path: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{host}_{path.strip('/').replace('/', '_')}")
    d = WORKSPACES / (safe[:120] or "ws")
    d.mkdir(parents=True, exist_ok=True)
    readme = d / "REMOTE.md"
    if not readme.exists():
        readme.write_text(
            f"""# Remote project context

You are helping with a project on SSH host `{host}`.
Remote working directory: `{path}`

Use the custom tools `ssh_run`, `ssh_read`, `ssh_write`, `ssh_list` for ALL file and shell work on that machine.
Do not assume local files mirror the remote tree.
""",
            encoding="utf-8",
        )
    return d


def _ssh_run(host: str, remote_cwd: str, command: str, timeout: int = SSH_TIMEOUT_SEC) -> str:
    if not is_safe_remote_path(remote_cwd):
        return "error: unsafe remote cwd"
    # Run inside project dir; keep shell simple and quoted.
    remote = f"cd {json.dumps(remote_cwd)} && ( {command} )"
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout=10",
                "-o",
                "ConnectionAttempts=1",
                "-o",
                "StrictHostKeyChecking=accept-new",
                host,
                "bash",
                "-lc",
                remote,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"error: ssh timeout after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"

    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + "\n…[truncated]"
    return f"exit={proc.returncode}\n{out}".strip()


def _make_tools(host: str, remote_path: str) -> dict[str, CustomTool]:
    def ssh_run(args: dict[str, Any], _ctx: CustomToolContext) -> str:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return "error: empty command"
        return _ssh_run(host, remote_path, cmd)

    def ssh_list(args: dict[str, Any], _ctx: CustomToolContext) -> str:
        rel = str(args.get("path") or ".").strip() or "."
        if ".." in rel.split("/"):
            return "error: unsafe path"
        return _ssh_run(host, remote_path, f"ls -la -- {json.dumps(rel)}")

    def ssh_read(args: dict[str, Any], _ctx: CustomToolContext) -> str:
        rel = str(args.get("path") or "").strip()
        if not rel or ".." in rel.split("/"):
            return "error: unsafe path"
        # Prefer relative to project; also allow absolute under /home
        target = rel if rel.startswith("/") else rel
        if target.startswith("/") and not is_safe_remote_path(target):
            return "error: unsafe absolute path"
        cmd = f"python3 - <<'PY'\nfrom pathlib import Path\np=Path({json.dumps(target)})\ndata=p.read_bytes()\nprint(data[:{MAX_OUTPUT_CHARS}].decode('utf-8','replace'))\nPY"
        return _ssh_run(host, remote_path, cmd)

    def ssh_write(args: dict[str, Any], _ctx: CustomToolContext) -> str:
        rel = str(args.get("path") or "").strip()
        content = args.get("content")
        if content is None:
            return "error: content required"
        if not rel or ".." in rel.split("/"):
            return "error: unsafe path"
        content_s = str(content)
        if len(content_s.encode("utf-8")) > 200_000:
            return "error: content too large"
        # Write via base64 to avoid quoting hell.
        import base64

        b64 = base64.b64encode(content_s.encode("utf-8")).decode("ascii")
        target = rel
        cmd = (
            f"python3 - <<'PY'\n"
            f"import base64\nfrom pathlib import Path\n"
            f"p=Path({json.dumps(target)})\n"
            f"p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.write_bytes(base64.b64decode({json.dumps(b64)}))\n"
            f"print('wrote', p, 'bytes', p.stat().st_size)\n"
            f"PY"
        )
        return _ssh_run(host, remote_path, cmd)

    return {
        "ssh_run": CustomTool(
            description=(
                f"Run a bash command on SSH host {host} inside {remote_path}. "
                "Use for git, python, nvidia-smi, tests, etc."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to run in the remote project directory",
                    }
                },
                "required": ["command"],
            },
            execute=ssh_run,
        ),
        "ssh_list": CustomTool(
            description=f"List files on {host} relative to {remote_path}.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path (default .)"}
                },
            },
            execute=ssh_list,
        ),
        "ssh_read": CustomTool(
            description=f"Read a text file on {host} (relative to {remote_path} or absolute).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
            },
            execute=ssh_read,
        ),
        "ssh_write": CustomTool(
            description=f"Write/overwrite a text file on {host} (relative to {remote_path}).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            execute=ssh_write,
        ),
    }


async def get_client() -> AsyncClient:
    global _client
    async with _client_lock:
        if _client is None:
            _client = await AsyncClient.launch_bridge(
                workspace=str(ROOT),
                timeout=90,
                allow_api_key_env_fallback=True,
            )
        return _client


async def close_client() -> None:
    global _client
    async with _client_lock:
        if _client is not None:
            try:
                await _client.aclose()
            except Exception:
                pass
            _client = None
    async with _sessions_lock:
        _sessions.clear()


@dataclass
class AgentSession:
    host: str
    path: str
    agent: Any
    workspace: Path
    created_at: float = field(default_factory=time.time)
    busy: bool = False
    busy_since: float | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return _session_key(self.host, self.path)


def _text_from_assistant_message(msg: SDKAssistantMessage) -> str:
    content = getattr(msg, "message", None)
    if content is None:
        return ""
    # SDKAssistantMessageContent may expose .text or .content blocks
    text = getattr(content, "text", None)
    if text:
        return str(text)
    parts = []
    for block in getattr(content, "content", None) or []:
        t = getattr(block, "text", None)
        if t:
            parts.append(str(t))
    return "".join(parts)


def _event_from_msg(msg: Any) -> dict[str, Any] | None:
    if isinstance(msg, TextDeltaUpdate):
        text = getattr(msg, "text", "") or ""
        if text:
            return {"type": "assistant", "text": text}
        return None
    if isinstance(msg, SDKAssistantMessage):
        text = _text_from_assistant_message(msg)
        if text:
            # Full message snapshot — UI streams deltas separately when available.
            return {"type": "assistant_final", "text": text}
        return None
    if isinstance(msg, SDKThinkingMessage):
        text = getattr(msg, "text", None) or ""
        if text:
            return {"type": "thinking", "text": text}
    if isinstance(msg, SDKToolUseMessage):
        name = getattr(msg, "name", None) or getattr(msg, "tool_name", None) or "tool"
        return {"type": "tool", "text": f"→ {name}"}
    if isinstance(msg, SDKStatusMessage):
        status = getattr(msg, "status", None) or getattr(msg, "message", None) or str(msg)
        return {"type": "status", "text": str(status)}
    t = getattr(msg, "type", None) or type(msg).__name__
    text = getattr(msg, "text", None)
    if text:
        return {"type": str(t), "text": str(text)}
    return None


async def ensure_session(host: str, path: str) -> AgentSession:
    if not is_safe_remote_path(path):
        raise ValueError("unsafe path")
    key = _session_key(host, path)
    async with _sessions_lock:
        existing = _sessions.get(key)
        if existing is not None:
            return existing

    client = await get_client()
    workspace = _workspace_dir(host, path)
    tools = _make_tools(host, path)
    agent = await client.create_agent(
        model="composer-2.5",
        api_key=_api_key(),
        name=f"{host} {path}",
        local=LocalAgentOptions(cwd=str(workspace), custom_tools=tools),
    )
    session = AgentSession(host=host, path=path, agent=agent, workspace=workspace)
    async with _sessions_lock:
        _sessions[key] = session
    return session


async def send_and_stream(host: str, path: str, message: str) -> AsyncIterator[dict[str, Any]]:
    session = await ensure_session(host, path)
    if session.busy:
        # Auto-unlock stuck sessions (e.g. killed client mid-run).
        if session.busy_since and (time.time() - session.busy_since) > 240:
            session.busy = False
            session.busy_since = None
        else:
            yield {"type": "error", "text": "агент уже занят — дождись ответа"}
            return
    session.busy = True
    session.busy_since = time.time()
    session.history.append({"role": "user", "text": message})
    yield {"type": "user", "text": message}
    try:
        prompt = (
            f"[Remote SSH host={host} cwd={path}]\n"
            f"Use ssh_* tools for remote work.\n\n"
            f"{message}"
        )
        run = await session.agent.send(prompt)
        assistant_bits: list[str] = []
        async for msg in run.stream():
            ev = _event_from_msg(msg)
            if ev:
                if ev["type"] == "assistant":
                    assistant_bits.append(ev["text"])
                    yield ev
                elif ev["type"] == "assistant_final":
                    if not assistant_bits:
                        assistant_bits.append(ev["text"])
                        yield {"type": "assistant", "text": ev["text"]}
                else:
                    yield ev
        result = await run.wait()
        final = getattr(result, "result", None) or "".join(assistant_bits)
        status = getattr(result, "status", None) or "finished"
        if final and not assistant_bits:
            yield {"type": "assistant", "text": str(final)}
            session.history.append({"role": "assistant", "text": str(final)})
        elif assistant_bits:
            session.history.append({"role": "assistant", "text": "".join(assistant_bits)})
        yield {"type": "done", "text": str(status), "result": str(final) if final else ""}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "text": f"{type(exc).__name__}: {exc}"}
    finally:
        session.busy = False
        session.busy_since = None


async def force_unlock(host: str, path: str) -> bool:
    s = _sessions.get(_session_key(host, path))
    if not s:
        return False
    s.busy = False
    s.busy_since = None
    return True


def session_info(host: str, path: str) -> dict[str, Any] | None:
    s = _sessions.get(_session_key(host, path))
    if not s:
        return None
    return {
        "host": s.host,
        "path": s.path,
        "busy": s.busy,
        "created_at": s.created_at,
        "history": s.history[-40:],
    }
