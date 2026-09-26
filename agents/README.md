# GPU Fleet push agent

В основной панели admin нажимает **Добавить сервер** → **Агент отправляет
данные**, указывает имя (например, `h200`) и сохраняет одноразовый ключ.

На GPU-сервере:

```bash
git clone https://github.com/Gricha1/gpu_profiler.git
cd gpu_profiler/agents
cp .env.example .env
# вставить endpoint, host и token, полученные в панели
docker compose up -d --build
```

Агент не открывает порт и отправляет снимок `nvidia-smi`, RAM и диска каждые
5 секунд. Токен хранится в `agents/.env` только на GPU-сервере; не добавляйте
этот файл в Git.
