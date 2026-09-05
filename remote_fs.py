#!/usr/bin/env python3
"""Remote FS helper: roots / list dirs / find git repos."""

from __future__ import annotations

import json
import os
import sys


def home() -> str:
    return os.path.expanduser("~")


def is_dir_ok(path: str) -> bool:
    return os.path.isdir(path) and not os.path.islink(path)


def list_dirs(path: str) -> dict:
    path = os.path.abspath(path)
    if not is_dir_ok(path):
        return {"ok": False, "error": "not a directory", "path": path, "entries": []}
    entries = []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": path, "entries": []}
    for name in names:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        if not is_dir_ok(full):
            continue
        has_git = os.path.isdir(os.path.join(full, ".git"))
        has_child_dirs = False
        try:
            for child in os.listdir(full):
                if child.startswith("."):
                    continue
                if is_dir_ok(os.path.join(full, child)):
                    has_child_dirs = True
                    break
        except OSError:
            pass
        entries.append(
            {
                "name": name,
                "path": full.replace("\\", "/"),
                "is_git": has_git,
                "has_children": has_child_dirs,
            }
        )
    parent = os.path.dirname(path.rstrip("/")) or "/"
    return {
        "ok": True,
        "path": path.replace("\\", "/"),
        "parent": parent.replace("\\", "/"),
        "home": home().replace("\\", "/"),
        "entries": entries,
    }


def find_repos(root: str, max_depth: int = 4) -> dict:
    root = os.path.abspath(root)
    repos = []
    root_depth = root.rstrip("/").count("/")

    for dirpath, dirnames, _filenames in os.walk(root):
        depth = dirpath.rstrip("/").count("/") - root_depth
        # prune hidden and deep
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if depth > max_depth:
            dirnames[:] = []
            continue
        if ".git" in dirnames or os.path.isdir(os.path.join(dirpath, ".git")):
            # if this dir is a git repo, record and don't descend into it deeply for nested? still allow
            repos.append(
                {
                    "name": os.path.basename(dirpath) or dirpath,
                    "path": dirpath.replace("\\", "/"),
                    "is_git": True,
                }
            )
            # skip descending into .git
            if ".git" in dirnames:
                dirnames.remove(".git")
    repos.sort(key=lambda r: r["path"].lower())
    return {
        "ok": True,
        "root": root.replace("\\", "/"),
        "home": home().replace("\\", "/"),
        "repos": repos,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: roots|list|repos [path]"}))
        return
    cmd = sys.argv[1]
    if cmd == "roots":
        h = home().replace("\\", "/")
        print(json.dumps({"ok": True, "home": h, "cwd": os.getcwd().replace("\\", "/")}))
        return
    if cmd == "list":
        path = sys.argv[2] if len(sys.argv) > 2 else home()
        print(json.dumps(list_dirs(path)))
        return
    if cmd == "repos":
        path = sys.argv[2] if len(sys.argv) > 2 else home()
        print(json.dumps(find_repos(path)))
        return
    print(json.dumps({"ok": False, "error": f"unknown cmd {cmd}"}))


if __name__ == "__main__":
    main()
