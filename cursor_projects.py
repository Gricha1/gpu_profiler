"""Discover and launch Cursor remote SSH projects."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECTS_FILE = ROOT / "projects.json"

_STORAGE_CANDIDATES = [
    Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "globalStorage" / "storage.json",
    Path.home() / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage" / "storage.json",
]

_WORKSPACE_STORAGE_CANDIDATES = [
    Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "workspaceStorage",
    Path.home() / "AppData" / "Roaming" / "Cursor" / "User" / "workspaceStorage",
]

_HOST_ALIASES = {
    "aicenter": "aicenter1",
    "aicenter1": "aicenter1",
    "labcomp": "lab_comp",
    "lab_comp": "lab_comp",
}


def _b64decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.b64decode(data + pad)


def _decode_ssh_host(authority: str) -> str | None:
    auth = urllib.parse.unquote(authority)
    if auth.startswith("ssh-remote+"):
        auth = auth[len("ssh-remote+") :]
    # Cursor Remote-SSH often hex-encodes {"hostName":"..."}.
    try:
        as_hex = bytes.fromhex(auth)
        payload = json.loads(as_hex.decode("utf-8"))
        host = payload.get("hostName") or payload.get("host")
        if isinstance(host, str) and host:
            return host
    except Exception:
        pass
    try:
        payload = json.loads(_b64decode(auth).decode("utf-8"))
        host = payload.get("hostName") or payload.get("host")
        if isinstance(host, str) and host:
            return host
    except Exception:
        pass
    return auth or None


def _parse_remote_uri(uri: str) -> tuple[str, str] | None:
    if "ssh-remote" not in uri:
        return None
    decoded = urllib.parse.unquote(uri)
    m = re.search(r"ssh-remote(?:%2B|\+)([^/]+)(/.*)$", uri)
    if not m:
        m = re.search(r"ssh-remote\+([^/]+)(/.*)$", decoded)
    if not m:
        return None
    host = _decode_ssh_host(m.group(1))
    path = urllib.parse.unquote(m.group(2))
    if not host or not path:
        return None
    return host, path


def _normalize_host(host: str) -> str:
    return _HOST_ALIASES.get(host, host)


def _hosts_equal(a: str, b: str) -> bool:
    return _normalize_host(a) == _normalize_host(b) or a == b


def _label_for(path: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return path
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[-2:])


def _encode_remote_authority(host: str) -> str:
    """Same authority format Remote-SSH uses in workspace URIs."""
    payload = json.dumps({"hostName": host}, separators=(",", ":")).encode("utf-8")
    return payload.hex()


def build_folder_uri(host: str, path: str, *, encoded: bool = True) -> str:
    if not path.startswith("/"):
        path = "/" + path
    authority = _encode_remote_authority(host) if encoded else host
    # Keep %2B form — matches Cursor workspace.json entries.
    return f"vscode-remote://ssh-remote%2B{authority}{path}"


def _looks_like_local_path(path: str) -> bool:
    if path.startswith("/") and not path.startswith("//"):
        # Unix absolute; on Windows treat as remote-only unless host=local.
        return os.name != "nt"
    return bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")


def _load_manual() -> dict[str, list[str]]:
    if not PROJECTS_FILE.exists():
        return {}
    try:
        data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        return out
    for host, paths in data.items():
        if not isinstance(paths, list):
            continue
        cleaned: list[str] = []
        for p in paths:
            if not isinstance(p, str):
                continue
            if host == "local":
                if _looks_like_local_path(p) or (os.name != "nt" and p.startswith("/")):
                    cleaned.append(p)
            elif p.startswith("/"):
                cleaned.append(p)
        out[str(host)] = cleaned
    return out


def _parse_file_uri(uri: str) -> Path | None:
    if not isinstance(uri, str) or not uri.startswith("file:"):
        return None
    parsed = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        # UNC: file://server/share
        path = f"//{parsed.netloc}{path}"
    if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    if not path:
        return None
    try:
        return Path(path)
    except Exception:
        return None


def _local_path_display(path: Path) -> str:
    # UI / JSON use forward slashes so options stay readable.
    text = str(path).replace("\\", "/")
    if os.name == "nt" and re.match(r"^[A-Za-z]:", text):
        text = text[0].upper() + text[1:]
    return text


def discover_local_projects() -> list[dict[str, str]]:
    """Local folders Cursor already opened (file://) + projects.json local."""
    found: dict[str, Path] = {}
    for uri in _iter_storage_uris():
        p = _parse_file_uri(uri)
        if p is None:
            continue
        try:
            if p.is_dir():
                found[_local_path_display(p)] = p
        except OSError:
            continue
    for raw in _load_manual().get("local", []):
        try:
            p = Path(raw)
            if p.is_dir():
                found[_local_path_display(p)] = p
        except OSError:
            continue

    items: list[dict[str, str]] = []
    for key in sorted(found.keys(), key=str.lower):
        p = found[key]
        items.append(
            {
                "path": key,
                "label": _label_for(key.replace("\\", "/")),
                "uri": p.as_uri(),
            }
        )
    return items


def is_safe_local_path(path: str) -> bool:
    if not path or "\x00" in path:
        return False
    try:
        p = Path(path)
    except Exception:
        return False
    if ".." in p.parts:
        return False
    if not p.is_absolute():
        return False
    try:
        return p.resolve().is_dir()
    except OSError:
        return False


def open_local_project(path: str) -> dict[str, Any]:
    if not is_safe_local_path(path):
        return {"ok": False, "error": "unsafe or missing path"}
    resolved = Path(path).resolve()
    # Same as remote hosts: open via folder-uri so Cursor IDE gets a real
    # workspace window (bare path often opens a broken/non-editor shell).
    folder_uri = resolved.as_uri()
    # --classic forces Cursor Editor Window (not Agents/Hub glass).
    launched = _launch_cursor(["-n", "--classic", "--folder-uri", folder_uri])
    if not launched.get("ok"):
        return launched
    return {
        "ok": True,
        "host": "local",
        "path": _local_path_display(resolved),
        "uri": folder_uri,
        "mode": "ide",
    }


def _workspace_storage_root() -> Path | None:
    for p in _WORKSPACE_STORAGE_CANDIDATES:
        if p.exists():
            return p
    return None


def _iter_storage_uris() -> list[str]:
    uris: list[str] = []
    for storage in _STORAGE_CANDIDATES:
        if not storage.exists():
            continue
        try:
            data = json.loads(storage.read_text(encoding="utf-8"))
        except Exception:
            continue
        assoc = data.get("profileAssociations", {}).get("workspaces", {})
        if isinstance(assoc, dict):
            uris.extend(str(k) for k in assoc)
        backup = data.get("backupWorkspaces", {})
        for folder in backup.get("folders", []) if isinstance(backup, dict) else []:
            if isinstance(folder, dict) and folder.get("folderUri"):
                uris.append(str(folder["folderUri"]))
        win = data.get("windowsState", {})
        if isinstance(win, dict):
            for w in win.get("openedWindows", []) or []:
                if isinstance(w, dict) and w.get("folder"):
                    uris.append(str(w["folder"]))
            last = win.get("lastActiveWindow")
            if isinstance(last, dict) and last.get("folder"):
                uris.append(str(last["folder"]))
        break

    ws_root = _workspace_storage_root()
    if ws_root:
        for d in ws_root.iterdir():
            wj = d / "workspace.json"
            if not wj.exists():
                continue
            try:
                folder = json.loads(wj.read_text(encoding="utf-8")).get("folder")
            except Exception:
                continue
            if isinstance(folder, str) and folder:
                uris.append(folder)
    return uris


def _authority_is_encoded(uri: str) -> bool:
    m = re.search(r"ssh-remote(?:%2B|\+)([^/]+)", uri)
    if not m:
        return False
    auth = urllib.parse.unquote(m.group(1))
    try:
        bytes.fromhex(auth)
        return True
    except Exception:
        return False


def _workspace_dir_for_uri(uri: str) -> Path | None:
    """Find workspaceStorage dir whose workspace.json folder matches uri (loose)."""
    ws_root = _workspace_storage_root()
    if not ws_root:
        return None
    target = _parse_remote_uri(uri)
    if not target:
        return None
    host, path = target
    best: tuple[float, Path] | None = None
    for d in ws_root.iterdir():
        wj = d / "workspace.json"
        if not wj.exists():
            continue
        try:
            folder = json.loads(wj.read_text(encoding="utf-8")).get("folder")
        except Exception:
            continue
        if not isinstance(folder, str):
            continue
        # Exact URI match first
        if folder == uri or urllib.parse.unquote(folder) == urllib.parse.unquote(uri):
            mtime = d.stat().st_mtime
            if best is None or mtime >= best[0]:
                best = (mtime, d)
            continue
        parsed = _parse_remote_uri(folder)
        if not parsed:
            continue
        if _hosts_equal(parsed[0], host) and parsed[1] == path and folder == uri:
            mtime = d.stat().st_mtime
            if best is None or mtime >= best[0]:
                best = (mtime, d)
    return best[1] if best else None


def resolve_folder_uri(host: str, path: str) -> str:
    """
    Pick the folder URI Cursor already knows for this host/path so chats bind
    to the existing workspace instead of creating a fresh one.
    """
    if not path.startswith("/"):
        path = "/" + path

    candidates: list[tuple[float, int, str]] = []
    # score: (mtime, encoded_bonus, uri)
    ws_root = _workspace_storage_root()
    seen: set[str] = set()

    def consider(uri: str, mtime: float = 0.0) -> None:
        if uri in seen:
            return
        seen.add(uri)
        parsed = _parse_remote_uri(uri)
        if not parsed:
            return
        if not _hosts_equal(parsed[0], host) or parsed[1] != path:
            return
        encoded_bonus = 1 if _authority_is_encoded(uri) else 0
        candidates.append((mtime, encoded_bonus, uri))

    if ws_root:
        for d in ws_root.iterdir():
            wj = d / "workspace.json"
            if not wj.exists():
                continue
            try:
                folder = json.loads(wj.read_text(encoding="utf-8")).get("folder")
            except Exception:
                continue
            if isinstance(folder, str) and folder:
                try:
                    mtime = d.stat().st_mtime
                    # Bigger state DB ≈ more history in that workspace.
                    db = d / "state.vscdb"
                    if db.exists():
                        mtime += min(db.stat().st_size / 1_000_000.0, 50.0)
                except OSError:
                    mtime = 0.0
                consider(folder, mtime)

    for uri in _iter_storage_uris():
        consider(uri, 0.0)

    if candidates:
        # Prefer encoded Remote-SSH authority (where old chats usually live);
        # among equals pick the richer/more recent workspaceStorage.
        candidates.sort(key=lambda t: (t[1], t[0]), reverse=True)
        return candidates[0][2]

    return build_folder_uri(host, path, encoded=True)


def discover_projects(hosts: list[str]) -> dict[str, list[dict[str, str]]]:
    wanted = {_normalize_host(h): h for h in hosts}
    for h in hosts:
        wanted[h] = h

    by_host: dict[str, set[str]] = {h: set() for h in hosts}

    for uri in _iter_storage_uris():
        parsed = _parse_remote_uri(uri)
        if not parsed:
            continue
        host_raw, path = parsed
        host_norm = _normalize_host(host_raw)
        target = wanted.get(host_raw) or wanted.get(host_norm)
        if not target:
            continue
        by_host.setdefault(target, set()).add(path)

    for host, paths in _load_manual().items():
        target = wanted.get(host) or wanted.get(_normalize_host(host))
        if not target:
            continue
        by_host.setdefault(target, set()).update(paths)

    result: dict[str, list[dict[str, str]]] = {}
    for host in hosts:
        paths = sorted(by_host.get(host, set()), key=lambda p: p.lower())
        items = []
        for p in paths:
            uri = resolve_folder_uri(host, p)
            items.append({"path": p, "label": _label_for(p), "uri": uri})
        result[host] = items
    return result


def find_cursor_cmd() -> str | None:
    which = shutil.which("cursor") or shutil.which("cursor.cmd")
    if which:
        return which
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "cursor"
        / "resources"
        / "app"
        / "bin"
        / "cursor.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor" / "Cursor.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _cursor_cli_argv() -> tuple[list[str], dict[str, str] | None] | None:
    """Prefer Cursor.exe + cli.js (same as cursor.cmd) so Windows launch is reliable."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor"
    exe = base / "Cursor.exe"
    cli = base / "resources" / "app" / "out" / "cli.js"
    if exe.exists() and cli.exists():
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env.pop("ELECTRON_NO_ASAR", None)
        return [str(exe), str(cli)], env
    cmd = find_cursor_cmd()
    if cmd:
        return [cmd], None
    return None


def is_safe_remote_path(path: str) -> bool:
    if not path.startswith("/") or ".." in path.split("/"):
        return False
    return bool(re.fullmatch(r"/[A-Za-z0-9_./\-]+", path))


def _launch_cursor(args: list[str]) -> dict[str, Any]:
    launch = _cursor_cli_argv()
    if not launch:
        return {"ok": False, "error": "cursor CLI not found"}
    argv, env = launch
    # Do NOT use CREATE_NO_WINDOW: it breaks Cursor CLI IPC, so Popen
    # succeeds but no IDE window appears.
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if env is not None:
        kwargs["env"] = env
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    try:
        subprocess.Popen([*argv, *args], **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def open_remote_project(host: str, path: str, uri: str | None = None) -> dict[str, Any]:
    if not is_safe_remote_path(path):
        return {"ok": False, "error": "unsafe path"}
    folder_uri = uri or resolve_folder_uri(host, path)
    launched = _launch_cursor(["-n", "--classic", "--folder-uri", folder_uri])
    if not launched.get("ok"):
        return launched
    return {
        "ok": True,
        "host": host,
        "path": path,
        "uri": folder_uri,
        "mode": "ide",
        "reused_workspace": folder_uri != build_folder_uri(host, path, encoded=False),
    }


def open_remote_agent(host: str, path: str, uri: str | None = None) -> dict[str, Any]:
    """Open standalone Cursor Agent/Chat window for the remote folder."""
    if not is_safe_remote_path(path):
        return {"ok": False, "error": "unsafe path"}
    folder_uri = uri or resolve_folder_uri(host, path)
    launched = _launch_cursor(["-n", "--chat", "--folder-uri", folder_uri])
    if not launched.get("ok"):
        return launched

    hwnd = None
    try:
        from window_capture import track_newest_cursor_window

        hwnd = track_newest_cursor_window(wait_sec=5.0)
    except Exception:
        hwnd = None

    return {
        "ok": True,
        "host": host,
        "path": path,
        "uri": folder_uri,
        "mode": "agent",
        "hwnd": hwnd,
        "reused_workspace": folder_uri != build_folder_uri(host, path, encoded=False),
    }
