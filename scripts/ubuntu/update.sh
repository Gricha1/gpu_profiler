#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/gpu_profiler"
cd "${ROOT}"

git fetch origin main
git merge --ff-only origin/main
"${ROOT}/.venv/bin/pip" install -r requirements-dev.txt
"${ROOT}/.venv/bin/python" -m pytest -q
install -m 0644 services/gpu-profiler-linux.service \
  "${HOME}/.config/systemd/user/gpu-profiler.service"
systemctl --user daemon-reload
systemctl --user restart gpu-profiler.service

sleep 3
systemctl --user is-active --quiet gpu-profiler.service
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ >/dev/null
echo "GPU Profiler update completed"
