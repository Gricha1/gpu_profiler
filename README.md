# GPU Profiler

Мониторинг GPU-серверов через SSH.

## Запуск на Ubuntu

Требуются Docker Engine, Git и настроенный `~/.ssh`.

```bash
git clone <REPOSITORY_URL> ~/gpu_profiler
cd ~/gpu_profiler
cp .env.example .env
bash scripts/ubuntu/docker-deploy.sh
```

- приложение: `http://<SERVER_IP>:8000/`
- статистика: `http://<SERVER_IP>:8001/` — только `admin` и пароль из `.env`

Обновление:

```bash
cd ~/gpu_profiler
git pull --ff-only origin main
bash scripts/ubuntu/docker-deploy.sh
```

Данные сохраняются в `data/`. Оба контейнера используют restart policy
`unless-stopped`. Подробности архитектуры: [servers_profiler.md](./servers_profiler.md).
