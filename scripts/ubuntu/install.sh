#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/gpu_profiler"
SERVICE_SOURCE="${ROOT}/services/gpu-profiler-linux.service"
SERVICE_DIR="${HOME}/.config/systemd/user"

if [[ ! -f "${ROOT}/app.py" || ! -f "${SERVICE_SOURCE}" ]]; then
  echo "Expected repository at ${ROOT}" >&2
  exit 1
fi

python3 -m venv "${ROOT}/.venv"
"${ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${ROOT}/.venv/bin/pip" install -r "${ROOT}/requirements.txt"
mkdir -p "${SERVICE_DIR}"
install -m 0644 "${SERVICE_SOURCE}" "${SERVICE_DIR}/gpu-profiler.service"
systemctl --user daemon-reload
systemctl --user enable --now gpu-profiler.service

sleep 3
systemctl --user --no-pager --full status gpu-profiler.service
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/ >/dev/null
echo "GPU Profiler is ready at http://$(hostname -I | awk '{print $1}'):8000/"
