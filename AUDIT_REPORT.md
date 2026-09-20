# GPU Monitor — итоговый аудит и рефакторинг

Дата: 2026-09-20. Каноническое описание рабочей архитектуры находится в
`AGENTS.md`; этот файл — отчёт по запрошенному аудиту.

## Результат

Сбор метрик отделён от HTTP, состояние каждого хоста независимо, последний
успешный снимок сохраняется при SSH-ошибке и перезапуске, DELETE→ADD защищён поколением
хоста, а все SSH-процессы приложения используют общий измеряемый лимит.
Административные и чувствительные API закрыты для удалённых клиентов без
токена. Фронтенд не держит несколько metrics-запросов одновременно и не
применяет ответ, начатый до ADD/DELETE.

## Подтверждённые проблемы и исправления

| Проблема | Причина | Исправление |
|---|---|---|
| Ошибка SSH уничтожала рабочие GPU/RAM/disk | Результат целиком заменял `_host_cache[host]` | `_apply_probe_result` сохраняет успешный payload и добавляет stale/error/timestamps |
| После рестарта снова появлялась «загрузка…» | Кэш существовал только в RAM | Последний успешный снимок атомарно хранится в `data/metrics/last_good/` и восстанавливается до первого SSH |
| HTTP инициировал полный обход | `/api/metrics` вызывал `_refresh_cache()` по возрасту | Единый `_metrics_scheduler`; endpoint только сериализует кэш |
| Браузеры умножали local/ZeroTier subprocess | Проверки запускались из endpoint | Они перенесены в планировщик и кэшируются |
| DELETE→ADD принимал старый результат | Проверялось только имя в `HOSTS` | `_host_generation` проверяется перед записью |
| ADD/DELETE могли потерять JSON-изменение | Read-modify-write без блокировки | `_config_lock` + временный файл + atomic replace |
| Route-потоки обходили host semaphore | Вложенный executor до 8 workers | `ssh_runtime` ограничивает все реальные SSH-процессы значением 3 |
| Успешный цикл повторял все SSH paths | Всегда вызывался `probe_host_paths()` | Fan-out только после ошибки, cache 300с |
| FS, SDK agent и VPN SSH были вне лимита | Независимые launchers | Все подключены к `ssh_runtime` |
| Не было измерения SSH-нагрузки | Не было единого runtime | `/api/diagnostics/ssh`: active/peak, attempts/min, errors, timeout, wait/duration |
| HTTP-ответ конфликтовал с ADD/DELETE | `_tickSeq` не учитывал мутации | Один in-flight request, abort 12с, `_serverMutationVersion` |
| Сетка пересоздавалась целиком | `grid.innerHTML` на каждом tick | Keyed cards; неизменившиеся DOM-узлы сохраняются |
| Startup-задачи не отменялись | Fire-and-forget + deprecated events | FastAPI lifespan, task registry, stop/cancel/gather |
| Управляющие API были общедоступны | Авторизации не было | Loopback trusted; remote admin требует token |
| `ssh_target` допускал option-like ввод | Недостаточная валидация | Безопасный alias/host/IP с необязательным `user@` |

## Итоговая схема

```text
FastAPI lifespan (ровно один worker)
  ├─ metrics scheduler (один экземпляр)
  │    ├─ due host A ─┐
  │    ├─ due host B ─┼─ asyncio Semaphore(3)
  │    └─ due host N ─┘       └─ ssh_runtime hard cap(3)
  │                                ├─ metrics + route diagnostics
  │                                ├─ remote file browser
  │                                ├─ SDK agent SSH tools
  │                                └─ legacy VPN SSH checks
  ├─ local GPU refresh (2s, cached)
  ├─ ZeroTier status refresh (30s, cached)
  └─ history collector (300s)

GET /api/metrics → snapshot from _host_cache only → browser keyed cards
```

Успешный хост планируется через 15с (`GPU_MONITOR_REFRESH_SEC`). Ошибка
получает backoff 30/60/120с с верхней границей
`GPU_MONITOR_BACKOFF_MAX_SEC`. Для имени одновременно существует не более
одной задачи. Отмена `asyncio.to_thread` не завершает уже запущенный `ssh.exe`:
subprocess имеет timeout, а поколение блокирует позднюю запись.

## Фактическая SSH-нагрузка

- Измеренный peak в тесте из 12 параллельных route-запусков: **3**.
- Авторитетный hard limit: **3 SSH-процесса** на backend-процесс.
- При восьми здоровых хостах и интервале 15с: около **32 metrics SSH/min**,
  без route fan-out.
- При ошибке конкретного хоста: metrics attempt, ограниченная диагностика и
  максимум один retry; следующие циклы замедляет backoff.
- Старое «27 SSH одновременно» не было измерено и неверно складывало
  последовательные фазы. Статический предел старого route fan-out был до 24,
  но runtime-телеметрии тогда не существовало.

Текущее точное значение возвращает `GET /api/diagnostics/ssh`.

## Производительность

Локальный benchmark с 8 cached hosts и 100 конкурентными вызовами функции
endpoint: **1.01 ms total, 0.010 ms/request, 0 SSH calls**. Это Python cache
path без HTTP transport. Раньше каждый request мог также запускать ZeroTier
CLI и инициировать refresh; сохранённого сравнимого baseline нет, поэтому
ускорение «в X раз» не заявляется.

## Тестирование

```powershell
python -m pytest -q
node --check static/app.js
python -m py_compile app.py host_paths.py remote_browse.py sdk_agent.py ssh_runtime.py
```

Результат: **26 passed**, JS/Python syntax checks успешны. Реальные серверы
не нагружались.

Покрыто: success→timeout с сохранением payload; stale-result; DELETE→ADD;
медленный хост; SSH hard cap/telemetry; cache-only реальный FastAPI handler;
реальный ADD handler; несколько клиентов; SQLite insert/read; viewer/admin;
идемпотентный startup; frontend race/DOM guards; atomic config.

Не проверялось автоматически: end-to-end SSH на лабораторных хостах,
визуальный screenshot regression, Scheduled Task/Amnezia/NetBird, реальная
остановка OpenSSH child tree на Windows. Это исключено, чтобы не менять и не
нагружать инфраструктуру.

## Безопасность и развёртывание

`scripts/start.ps1` слушает только `127.0.0.1:8765` и запускает один uvicorn
worker без `--reload`. Для удалённого доступа задайте длинный случайный
`GPU_MONITOR_ADMIN_TOKEN`: read-only metrics/history/status доступны, а
hosts/routes/proxy/file-browser/agent/config требуют bearer- или
`X-Admin-Token`. Не публикуйте приложение прямо в интернет; для лабораторной
сети нужен reverse proxy с TLS и пользовательской аутентификацией. Несколько
uvicorn workers не поддерживаются: cache/scheduler/limiter process-local.

## Изменённые файлы

- `app.py` — scheduler, host state/generations, lifespan, admin guard,
  cache-only HTTP, SSH diagnostics, безопасный ADD/DELETE.
- `ssh_runtime.py` — общий SSH limiter и telemetry.
- `host_paths.py`, `remote_browse.py`, `sdk_agent.py` — общий SSH runtime.
- `static/app.js`, `static/index.html` — polling/mutation guards, keyed cards,
  stale UI, asset v15.
- `test_audit_architecture.py`, `test_per_host_cache.py`, `pytest.ini` — tests.
- `AGENTS.md` и этот отчёт — документация.

## Запуск и проверка

```powershell
cd C:\Grisha\mipt\asp\NIR\servers\gpu_monitor
python -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
Invoke-RestMethod http://127.0.0.1:8765/api/metrics
Invoke-RestMethod http://127.0.0.1:8765/api/diagnostics/ssh
python -m pytest -q
```

Остановка: `scripts\stop.ps1`; он проверяет конкретный PID/process command
line и не завершает посторонние Python-процессы.

## Оставшиеся ограничения

1. Process-local state требует ровно один backend worker.
2. Авторизация — минимальный viewer/admin barrier, не полноценная система
   пользователей/ролей; общему сервису нужен внешний identity-aware proxy.
3. Уже запущенный `ssh.exe` не останавливается одной отменой asyncio-задачи;
   его ограничивает timeout, а результат защищён поколением.
4. Старые `_refresh_cache`/`_collect_incremental` оставлены как compatibility
   helpers для прежних тестов, production endpoints их не вызывают.
