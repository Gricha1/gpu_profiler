#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/gpu_profiler"
IMAGE="gpu-profiler:local"
CONTAINER="gpu-profiler"
DEBUG_CONTAINER="gpu-profiler-debug"
DOCKER_SOCKET="/var/run/docker.sock"

cd "${ROOT}"
mkdir -p data logs runtime config/local
for name in host_paths projects protected_nets; do
  if [[ ! -f "config/local/${name}.json" ]]; then
    if [[ -f "${name}.json" ]]; then
      cp "${name}.json" "config/local/${name}.json"
    else
      cp "config/examples/${name}.example.json" "config/local/${name}.json"
    fi
  fi
done

docker build \
  --network host \
  --build-arg "APP_UID=$(id -u)" \
  --build-arg "APP_GID=$(id -g)" \
  --tag "${IMAGE}" .

if docker container inspect "${CONTAINER}" >/dev/null 2>&1; then
  docker container rm --force "${CONTAINER}"
fi
if docker container inspect "${DEBUG_CONTAINER}" >/dev/null 2>&1; then
  docker container rm --force "${DEBUG_CONTAINER}"
fi

env_args=()
if [[ -f .env ]]; then
  env_args=(--env-file "${ROOT}/.env")
fi

docker run --detach \
  --name "${CONTAINER}" \
  --restart unless-stopped \
  --network host \
  --env GPU_MONITOR_CONFIG_DIR=/app/config-local \
  "${env_args[@]}" \
  --volume "${ROOT}/data:/app/data" \
  --volume "${ROOT}/logs:/app/logs" \
  --volume "${ROOT}/runtime:/app/runtime" \
  --volume "${ROOT}/config/local:/app/config-local" \
  --volume "${HOME}/.ssh:/home/app/.ssh:ro" \
  "${IMAGE}"

docker run --detach \
  --name "${DEBUG_CONTAINER}" \
  --restart unless-stopped \
  --network host \
  --user 0:0 \
  --health-cmd "curl --fail --silent http://127.0.0.1:8001/ >/dev/null || exit 1" \
  "${env_args[@]}" \
  --volume "${ROOT}/data:/app/data" \
  --volume "${ROOT}:/host-repo" \
  --volume "${DOCKER_SOCKET}:${DOCKER_SOCKET}" \
  --volume "/usr/bin/docker:/usr/bin/docker:ro" \
  "${IMAGE}" \
  python -m uvicorn debug_app:app --host 0.0.0.0 --port 8001 --workers 1

for _ in {1..30}; do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null \
    && curl --fail --silent --max-time 3 http://127.0.0.1:8001/ >/dev/null; then
    echo "GPU Profiler: http://$(hostname -I | awk '{print $1}'):8000/"
    echo "Analytics: http://$(hostname -I | awk '{print $1}'):8001/"
    exit 0
  fi
  sleep 1
done

docker logs --tail 100 "${CONTAINER}" >&2
exit 1
