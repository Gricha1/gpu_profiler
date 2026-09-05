#!/usr/bin/env python3
"""Run on remote host: GPU stats, compute processes + owners, RAM."""

import csv
import io
import os
import subprocess


def run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, universal_newlines=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


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

    print("---USERS---")
    for pid in sorted(pids, key=lambda x: int(x) if x.isdigit() else 0):
        user = ""
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("Uid:"):
                        uid = int(line.split()[1])
                        try:
                            import pwd

                            user = pwd.getpwuid(uid).pw_name
                        except Exception:
                            user = str(uid)
                        break
        except OSError:
            pass
        if not user:
            out = run(["ps", "-o", "user=", "-p", pid]).strip()
            user = out.split()[0] if out else "?"
        print(f"{pid},{user}")

    print("---RAM---")
    print(run(["free", "-b"]).rstrip())

    home = os.path.expanduser("~")
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


if __name__ == "__main__":
    main()
