#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/gpu_profiler"
IMAGE="gpu-profiler:local"
CONTAINER="gpu-profiler"

cd "${ROOT}"
mkdir -p data logs runtime

docker build \
  --build-arg "APP_UID=$(id -u)" \
  --build-arg "APP_GID=$(id -g)" \
  --tag "${IMAGE}" .

if docker container inspect "${CONTAINER}" >/dev/null 2>&1; then
  docker container rm --force "${CONTAINER}"
fi

env_args=()
if [[ -f .env ]]; then
  env_args=(--env-file "${ROOT}/.env")
fi

docker run --detach \
  --name "${CONTAINER}" \
  --restart unless-stopped \
  --network host \
  "${env_args[@]}" \
  --volume "${ROOT}/data:/app/data" \
  --volume "${ROOT}/logs:/app/logs" \
  --volume "${ROOT}/runtime:/app/runtime" \
  --volume "${HOME}/.ssh:/home/app/.ssh:ro" \
  "${IMAGE}"

for _ in {1..30}; do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8000/ >/dev/null; then
    echo "GPU Profiler is ready at http://$(hostname -I | awk '{print $1}'):8000/"
    exit 0
  fi
  sleep 1
done

docker logs --tail 100 "${CONTAINER}" >&2
exit 1
