# GPU Profiler

FastAPI-панель мониторинга GPU/RAM серверов через SSH.

## Ubuntu: канонический запуск через Docker

Контейнер использует сеть хоста, поэтому видит те же маршруты NetBird,
ZeroTier и OpenVPN. Данные пользователей, конфигурация и SSH-ключи не входят в
образ и подключаются с хоста.

Требования: Ubuntu 22.04/24.04, Git, Docker Engine и настроенный `~/.ssh`.
Пользователь должен иметь доступ к Docker (`docker` group).

### Первая установка

```bash
git clone <REPOSITORY_URL> ~/gpu_profiler
cd ~/gpu_profiler
cp .env.example .env
# При необходимости измените пароль администратора и остальные параметры в .env
bash scripts/ubuntu/docker-deploy.sh
```

Открыть:

```text
http://<IP_UBUNTU>:8000/
```

### Обновление

```bash
cd ~/gpu_profiler
git fetch origin main
git merge --ff-only origin/main
bash scripts/ubuntu/docker-deploy.sh
```

Скрипт собирает новый образ, заменяет только контейнер и проверяет HTTP.
Каталоги `data/`, `logs/`, `runtime/`, `.env` и SSH keys остаются на хосте.
Список серверов хранится в SQLite и при старте восстанавливает runtime JSON.

Если установлен Docker Compose plugin, эквивалентный ручной запуск:

```bash
docker compose up -d --build
```

### Управление и диагностика

```bash
docker ps --filter name=gpu-profiler
docker logs -f gpu-profiler
docker restart gpu-profiler
docker stop gpu-profiler
curl -I http://127.0.0.1:8000/
```

## Подключённые данные

- `./data:/app/data` — пользователи, IP bindings, история и last-good cache.
- `host_paths.json`, `projects.json`, `protected_nets.json` — versioned defaults внутри образа.
- `~/.ssh:/home/app/.ssh:ro` — SSH aliases и ключи, только чтение.
- `network_mode: host` — доступ к overlay/VPN-маршрутам Ubuntu-хоста.

Секреты находятся только в `.env`; файл исключён из Git.

## Текущий deployment

`fic_comp`: **http://192.168.194.193:8000/**

Каталог: `/home/gregory/gpu_profiler`, контейнер: `gpu-profiler`.

## Windows

Windows-вариант сохранён для локальных Amnezia/NetBird/ZeroTier-инструментов:

```powershell
pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

URL: `http://127.0.0.1:8765/`.

## Проверка разработки

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m py_compile app.py user_config.py
node --check static/app.js
```

Архитектура и история: [servers_profiler.md](./servers_profiler.md).
Инварианты сопровождения: [AGENTS.md](./AGENTS.md).
