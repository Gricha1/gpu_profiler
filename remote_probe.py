#!/usr/bin/env python3
"""Run on remote host: GPU stats, compute processes + owners, RAM."""

import csv
import io
import json
import os
import pwd
import re
import subprocess


def run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, universal_newlines=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


_CONTAINER_ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{12,64}(?![0-9a-f])", re.I)
_HOME_MOUNT_RE = re.compile(
    r"(?:^|/)(?:home|homes|data/homes|mnt/data)/([A-Za-z0-9][A-Za-z0-9_.-]*)(?:/|$)"
)


def _pid_user(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                    try:
                        return pwd.getpwuid(uid).pw_name
                    except Exception:
                        return str(uid)
    except OSError:
        pass
    out = run(["ps", "-o", "user=", "-p", pid]).strip()
    return out.split()[0] if out else "?"


def _mount_owner(source: object) -> str | None:
    """Return the account encoded by a conventional host home bind mount."""
    match = _HOME_MOUNT_RE.search(str(source or "").replace("\\", "/"))
    if not match:
        return None
    owner = match.group(1)
    return None if owner == "root" else owner


def _container_candidates_for_pids(pids: set[str]) -> dict[str, set[str]]:
    """Return Docker/container-runtime IDs visible in each process cgroup."""
    pid_candidates: dict[str, set[str]] = {}
    for pid in pids:
        try:
            cgroup = open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        candidates = {m.group(0).lower() for m in _CONTAINER_ID_RE.finditer(cgroup)}
        if candidates:
            pid_candidates[pid] = candidates
    return pid_candidates


def _known_home_users() -> set[str]:
    """Read user directory names from the configured host home root."""
    home_dir = os.environ.get("GPU_MONITOR_PROBE_HOMES_DIR") or "/home"
    try:
        return {
            entry
            for entry in os.listdir(home_dir)
            if os.path.isdir(os.path.join(home_dir, entry))
        }
    except OSError:
        return set()


def _mountinfo_owner(pid: str, known_users: set[str]) -> str | None:
    """Infer one user from bind-mount roots in a container's mount namespace."""
    owners: set[str] = set()
    try:
        lines = open(f"/proc/{pid}/mountinfo", encoding="utf-8", errors="replace").readlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        # mountinfo fields 4/5 are the filesystem root and mount point.
        for value in (fields[3], fields[4]):
            owner = _mount_owner(value)
            if not owner:
                for segment in str(value).strip("/").split("/"):
                    if segment in known_users:
                        owner = segment
                        break
            if owner:
                owners.add(owner)
    return next(iter(owners)) if len(owners) == 1 else None


def _mountinfo_owners_for_pids(pid_candidates: dict[str, set[str]]) -> dict[str, str]:
    """Safe agent fallback: no Docker socket, only already-readable /proc data."""
    known_users = _known_home_users()
    if not known_users:
        return {}
    return {
        pid: owner
        for pid in pid_candidates
        for owner in [_mountinfo_owner(pid, known_users)]
        if owner
    }


def _docker_owners_for_pids(pid_candidates: dict[str, set[str]]) -> dict[str, str]:
    """Best-effort root PID -> owner from a uniquely mounted user home.

    Docker does not retain the shell user that created a container.  A bind
    mount below /home/<user>, /data/homes/<user>, or /mnt/data/<user> is a
    useful, explicitly marked inference when lab users run their own project.
    Missing Docker permissions or ambiguous mounts deliberately return no
    result, leaving the process shown as root.
    """
    if not pid_candidates:
        return {}

    running_ids = {
        line.strip().lower()
        for line in run(["docker", "ps", "--no-trunc", "--format", "{{.ID}}"]).splitlines()
        if line.strip()
    }
    inspect_ids = sorted({candidate for values in pid_candidates.values() for candidate in values if candidate in running_ids})
    if not inspect_ids:
        return {}
    try:
        inspected = json.loads(run(["docker", "inspect", *inspect_ids]))
    except (TypeError, ValueError):
        return {}

    owner_by_container: dict[str, str] = {}
    for container in inspected if isinstance(inspected, list) else []:
        container_id = str(container.get("Id") or "").lower()
        owners = {
            owner
            for mount in container.get("Mounts") or []
            if isinstance(mount, dict) and mount.get("Type") == "bind"
            for owner in [_mount_owner(mount.get("Source"))]
            if owner
        }
        if container_id and len(owners) == 1:
            owner_by_container[container_id] = owners.pop()

    return {
        pid: owner_by_container[container_id]
        for pid, candidates in pid_candidates.items()
        for container_id in candidates
        if container_id in owner_by_container
    }


def main() -> None:
    print(
        run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,uuid",
                "--format=csv,noheader,nounits",
            ]
        ).rstrip()
    )
    print("---PROCS---")
    procs = run(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,used_gpu_memory,process_name",
            "--format=csv,noheader,nounits",
        ]
    )
    print(procs.rstrip())

    pids = set()
    for row in csv.reader(io.StringIO(procs)):
        if len(row) >= 2:
            pids.add(row[1].strip())

    pid_users = {pid: _pid_user(pid) for pid in pids}
    root_pids = {pid for pid, user in pid_users.items() if user == "root"}
    pid_candidates = _container_candidates_for_pids(root_pids)
    docker_owners = _docker_owners_for_pids(pid_candidates)
    mountinfo_owners = _mountinfo_owners_for_pids(pid_candidates)

    print("---USERS---")
    for pid in sorted(pids, key=lambda x: int(x) if x.isdigit() else 0):
        user = pid_users.get(pid) or "?"
        if user == "root":
            owner = docker_owners.get(pid) or mountinfo_owners.get(pid)
            if owner:
                user = f"docker: {owner}"
        print(f"{pid},{user}")

    print("---RAM---")
    print(run(["free", "-b"]).rstrip())

    home = os.environ.get("GPU_MONITOR_PROBE_HOME") or os.path.expanduser("~")
    print("---DISK---")
    # Home mount first (for MY HOME %), then extra large volumes if present.
    disk_targets = [home]
    for extra in ("/data", "/data2"):
        if extra not in disk_targets and os.path.isdir(extra):
            disk_targets.append(extra)
    print(run(["df", "-B1", "-P", *disk_targets]).rstrip())
    print("---HOME---")
    # Cap runtime so slow NFS homes don't stall the whole probe.
    # du often exits 1 on permission-denied subdirs but still prints a total.
    home_line = ""
    for cmd in (
        ["timeout", "12", "du", "-sb", home],
        ["du", "-sb", home],
    ):
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                timeout=15,
                check=False,
            )
            line = (proc.stdout or "").strip().splitlines()
            if line:
                # Prefer last non-empty line: "BYTES\tPATH"
                home_line = line[-1].strip()
                if home_line and home_line[0].isdigit():
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    print(home_line if home_line else f"0\t{home}")

    print("---ALL_HOMES---")
    # Push agents run with a short heartbeat. On a large GPU server a complete
    # sequential du of every user home can take minutes, so an agent may opt
    # out while still reporting the configured account home above.
    if os.environ.get("GPU_MONITOR_PROBE_SKIP_ALL_HOMES") == "1":
        return
    # Collect disk usage for all users in /home
    home_dir = os.environ.get("GPU_MONITOR_PROBE_HOMES_DIR") or "/home"
    if os.path.isdir(home_dir):
        try:
            users = [d for d in os.listdir(home_dir) if os.path.isdir(os.path.join(home_dir, d))]
            for user in sorted(users):
                user_path = os.path.join(home_dir, user)
                user_line = ""
                for cmd in (
                    ["timeout", "10", "du", "-sb", user_path],
                    ["du", "-sb", user_path],
                ):
                    try:
                        proc = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            universal_newlines=True,
                            timeout=12,
                            check=False,
                        )
                        line = (proc.stdout or "").strip().splitlines()
                        if line:
                            user_line = line[-1].strip()
                            if user_line and user_line[0].isdigit():
                                break
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        continue
                if user_line:
                    print(f"{user}\t{user_line}")
                else:
                    print(f"{user}\t0\t{user_path}")
        except OSError:
            pass


if __name__ == "__main__":
    main()
