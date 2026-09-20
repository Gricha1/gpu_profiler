# GPU Profiler

FastAPI-панель мониторинга GPU/RAM серверов через SSH.

## Как запускается на Ubuntu

Канонический production-вариант: Python virtual environment + systemd user
service. Сам GPU Profiler **не запускается в Docker**. Docker использовался на
`fic_comp` только для отдельного NetBird-клиента.

Требования: Ubuntu 22.04/24.04, Python 3.10+, `git`, `openssh-client`,
`python3-venv` и настроенные SSH aliases/keys.

### Первая установка

Репозиторий должен находиться в `~/gpu_profiler`, поскольку этот путь использует
service unit:

```bash
sudo apt update
sudo apt install -y curl git openssh-client python3 python3-venv
git clone <REPOSITORY_URL> ~/gpu_profiler
cd ~/gpu_profiler
bash scripts/ubuntu/install.sh
```

Проверка и адрес:

```bash
systemctl --user status gpu-profiler.service
curl -I http://127.0.0.1:8000/
```

```text
http://<IP_UBUNTU>:8000/
```

Чтобы сервис запускался после reboot до интерактивного входа:

```bash
sudo loginctl enable-linger "$USER"
```

### Обновление

```bash
cd ~/gpu_profiler
bash scripts/ubuntu/update.sh
```

Скрипт принимает только fast-forward `main`, обновляет зависимости, запускает
тесты, перезапускает сервис и проверяет HTTP endpoint.

### Управление и логи

```bash
systemctl --user restart gpu-profiler.service
systemctl --user stop gpu-profiler.service
journalctl --user -u gpu-profiler.service -f
```

## Конфигурация

- `.env` — локальные секреты и параметры; шаблон `.env.example`.
- `host_paths.json` — runtime-зеркало SSH-конфигураций.
- `data/users.sqlite3` — пользователи, IP bindings и видимость серверов.
- `~/.ssh/config` и SSH keys всегда находятся вне репозитория.

`data/`, `.env`, логи и ключи исключены из Git.

## Текущий deployment

`fic_comp`: **http://192.168.194.193:8000/**

Каталог: `/home/gregory/gpu_profiler`. Сервис:
`~/.config/systemd/user/gpu-profiler.service`.

## Windows

Windows-вариант сохранён для локальных VPN/NetBird/ZeroTier/Amnezia функций:

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

Полная архитектура и история исправлений: [servers_profiler.md](./servers_profiler.md).
Инварианты для сопровождающих: [AGENTS.md](./AGENTS.md).
