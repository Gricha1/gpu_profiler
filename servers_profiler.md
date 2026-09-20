# GPU Profiler — каноническая архитектура и полный отчёт

Актуально на 2026-09-20. Проект расположен в `servers/gpu_monitor`, рабочая
ветка — `main`.

Этот документ объединяет понятное описание текущей архитектуры, историю
аудита, выполненные исправления, результаты тестов и инструкции эксплуатации.
Обязательные для автоматизированных агентов ограничения безопасности остаются
зафиксированы в `AGENTS.md`; при противоречии правила безопасности из
`AGENTS.md` имеют приоритет.

---

## 1. Назначение

GPU Profiler — локальная FastAPI-панель для наблюдения за GPU-серверами
лаборатории. Она показывает:

- GPU, VRAM, utilization и процессы на каждой карте;
- пользователей GPU-процессов;
- RAM, CPU, диски и использование домашних каталогов;
- доступность SSH-хостов и альтернативных сетевых путей;
- read-only состояние NetBird, ZeroTier, OpenVPN и AmneziaVPN;
- состояние Mesh Route Watcher и селективного ChatGPT/Yandex proxy;
- историю GPU-метрик;
- проекты, файловый браузер и опциональный Cursor SDK agent.

Backend работает на Python/FastAPI, frontend — одна HTML-страница с vanilla
JavaScript без этапа сборки.

Приложение поддерживает пользователей с отдельной конфигурацией серверов. На
Ubuntu unit слушает `0.0.0.0:8000`, локальный Windows launcher —
`127.0.0.1:8765`. Публиковать порт следует только в доверенной overlay/LAN сети
либо через защищённый reverse proxy.

---

## 2. Запуск и жизненный цикл

Канонический production-запуск на Ubuntu:

```bash
git clone <REPOSITORY_URL> ~/gpu_profiler
cd ~/gpu_profiler
bash scripts/ubuntu/docker-deploy.sh
```

Приложение работает в контейнере `gpu-profiler` с restart policy
`unless-stopped` и host networking. Изменяемые данные и SSH-конфигурация
подключаются с хоста. Обновление после fast-forward `main`:

```bash
cd ~/gpu_profiler
bash scripts/ubuntu/docker-deploy.sh
```

Локальный запуск на Windows:

```powershell
cd C:\Grisha\mipt\asp\NIR\servers\gpu_monitor
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

`scripts/start.ps1`:

1. Проверяет, не слушает ли уже `127.0.0.1:8765`.
2. Запускает один процесс:
   `python -m uvicorn app:app --host 127.0.0.1 --port 8765`.
3. Перенаправляет stdout/stderr в `logs/`.
4. Ждёт готовности HTTP и записывает PID listener в
   `logs/gpu_profiler.pid`.

Остановка:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
```

Скрипт завершает только PID, который действительно является Python/uvicorn
процессом этого приложения. Массовое завершение Python-процессов запрещено.

FastAPI использует lifespan вместо deprecated `@app.on_event`. На startup
создаётся ровно один scheduler, локальный collector, ZeroTier collector,
quota refresh и history collector. Все задачи регистрируются. На shutdown
выставляется stop event, задачи отменяются и ожидаются через `gather`.

Повторный startup в одном процессе идемпотентен и не создаёт второй набор
collectors.

### Ограничение по workers

Кэш, scheduler, поколения хостов и SSH limiter находятся в памяти процесса.
Поэтому поддерживается ровно один uvicorn worker и запуск без `--reload`.
Несколько workers создадут независимые SSH-сборщики и не поддерживаются без
внешнего координатора/хранилища.

---

## 3. Итоговая архитектура сбора метрик

```text
FastAPI lifespan — один backend worker
│
├─ _metrics_scheduler() — единственный планировщик remote-метрик
│   ├─ host A: собственный cache/state/due time/backoff/generation
│   ├─ host B: собственный cache/state/due time/backoff/generation
│   └─ host N: собственный cache/state/due time/backoff/generation
│          │
│          ├─ не более одной задачи на имя хоста
│          ├─ asyncio.Semaphore(3) на host probes
│          └─ ssh_runtime hard cap(3) на реальные ssh.exe
│
├─ local GPU refresh — каждые 2 секунды, независимо от remote SSH
├─ ZeroTier status refresh — каждые 30 секунд
├─ GPU history collector — каждые 5 минут
└─ persisted last-good cache — data/metrics/last_good/<host>.json

GET /api/metrics
└─ только читает готовый _host_cache
   └─ не создаёт SSH, subprocess или полный обход
```

Число браузеров и HTTP-запросов не влияет на частоту SSH. Каждый хост
обновляется независимо: долгий timeout одного сервера не задерживает запись
результатов остальных.

### Частота и backoff

- `GPU_MONITOR_REFRESH_SEC=15` — базовый интервал после успешного probe.
- После ошибки используется backoff 30/60/120 секунд.
- `GPU_MONITOR_BACKOFF_MAX_SEC=120` задаёт верхнюю границу.
- Следующий запуск планируется после завершения предыдущего.
- `_host_tasks` не допускает двух одновременных probes одного хоста.

### Состояние одного сервера

В `_host_cache[host]` хранятся:

- последний успешный GPU/RAM/disk payload;
- `last_success_at`;
- `last_attempt_at`;
- `polling`;
- `connection_ok` и `reachable`;
- последняя ошибка;
- `stale`;
- последний известный список сетевых путей.

Ошибка SSH не очищает успешные метрики. Карточка продолжает показывать
последний снимок и отмечает его как stale.

### Непрерывность после перезапуска

Каждый успешный снимок атомарно сохраняется в:

```text
data/metrics/last_good/<host>.json
```

При startup эти снимки загружаются до первого SSH-запроса. Они сразу
показываются как stale с состоянием «обновление после запуска», поэтому уже
известная карточка не возвращается в пустое состояние «загрузка…».

Файлы являются runtime-данными, находятся вне Git и не включаются в архив
исходников.

### Защита DELETE → ADD

Для каждого имени существует `_host_generation`. Probe запоминает поколение
при запуске и перед записью сравнивает его с текущим. DELETE и последующий ADD
увеличивают поколение, поэтому завершившийся старый `ssh.exe` не может записать
результат в новую карточку с тем же именем.

Конфигурация `host_paths.json` изменяется под `_config_lock` и записывается
через временный файл с atomic replace.

---

## 4. SSH: источники, лимит и диагностика

В приложении найдены следующие источники SSH:

1. Основной `remote_probe.py` для GPU/RAM/disk.
2. Диагностика альтернативных путей в `host_paths.py`.
3. Retry через лучший живой путь после ошибки preferred route.
4. Remote file browser.
5. Cursor SDK agent tools `ssh_run/read/write/list`.
6. Legacy VPN/VPS preflight checks.
7. Пользовательские операции открытия remote-проекта.

Основные, route, filesystem, agent и VPN SSH проходят через единый
`ssh_runtime.py`.

```text
GPU_MONITOR_SSH_MAX_ACTIVE=3
```

Это жёсткий process-wide предел активных SSH-процессов внутри одного backend.
`ThreadPoolExecutor` диагностики путей также не шире этого значения, поэтому
нет очереди из восьми заблокированных потоков на каждый сервер.

### Разделение metrics и route diagnostics

При успешном preferred metrics probe все альтернативные маршруты заново не
проверяются. Используется последний route snapshot. Полная диагностика
запускается только после ошибки основного пути; её cache живёт 300 секунд.

### Таймауты

- обычный SSH connect: 8 секунд;
- ProxyJump/lab path: 14 секунд;
- remote metrics subprocess: connect timeout + 25 секунд;
- remote filesystem: 24 секунды с явным `kill()` и `wait()`;
- отмена asyncio-задачи не считается остановкой sync `ssh.exe`: его завершает
  subprocess timeout, а generation guard блокирует позднюю запись.

### Фактическая нагрузка

Автоматизированный тест запустил 12 конкурентных route probes. Измеренный
максимум одновременно активных SSH-процессов — **3**.

При восьми здоровых хостах и интервале 15 секунд ожидается около 32 metrics
SSH-подключений в минуту. Route fan-out в штатный цикл не входит. Ошибочный
хост замедляется backoff.

Старое утверждение «27 SSH одновременно» не было измерением и неправильно
складывало последовательные стадии metrics и route probing. Статический
верхний предел старой route-фазы составлял до 24 процессов, но runtime-счётчика
тогда не существовало.

### Runtime telemetry

Локальный администратор может открыть:

```text
GET /api/diagnostics/ssh
```

Ответ содержит:

- `active` и `peak_active`;
- hard limit;
- connections за последнюю минуту;
- attempts/timeouts/errors по хостам;
- среднее ожидание SSH slot;
- среднюю длительность;
- число route diagnostics.

Секреты, ключи и содержимое команд в telemetry не записываются.

---

## 5. Frontend и непрерывное отображение карточек

Frontend подключается как `static/app.js?v=23`.

Исправлены три независимые гонки:

1. Одновременно выполняется не более одного `GET /api/metrics`.
2. HTTP-запрос ограничен 12 секундами через `AbortController`; новый polling
   не накапливается, пока предыдущий запрос не завершён.
3. `_serverMutationVersion` запрещает применять ответ, начатый до ADD/DELETE.

Подтверждённые ADD/DELETE дополнительно хранятся как локальный overlay в
`localStorage`. Поэтому старый aggregate snapshot не может временно скрыть
только что добавленный `ml3/ml4` или вернуть удалённую карточку. Backend может
обновлять содержимое optimistic-карточки, но не отменять пользовательскую
мутацию.

Дополнительно `retainLastKnownMetrics()` не позволяет пустому placeholder или
временной SSH-ошибке заменить уже показанные GPU/RAM/disk. Это защищает UI даже
при работе со старым backend до его перезапуска.

Для хоста без успешных метрик действует приоритет состояния: успешный ответ,
затем последняя конкретная ошибка соединения, и только затем placeholder
«загрузка…». Конкретная ошибка сохраняется по имени хоста в `localStorage`,
поэтому последующий промежуточный snapshot не стирает `Connection timed out`.
Это влияет только на отображение: периодический scheduler продолжает проверять
соединение и заменит ошибку актуальными метриками сразу после восстановления.

Сетка больше не пересоздаётся целиком через `grid.innerHTML`. Карточки
сопоставляются по `data-host`; неизменившиеся DOM-узлы сохраняются. Поэтому не
теряются scroll, открытое меню и выбранный пользователь HOME.

Если открыто меню шестерёнки, polling обновляет только status badges и не
заменяет/не перемещает карточку. Позиции окна и scroll-контейнера сохраняются
при обычном обновлении. Это устраняет прыжок правой карточки/страницы наверх.

Состояния карточки:

- первый запуск без какого-либо известного измерения;
- polling;
- online/fresh;
- stale с последними метриками;
- timeout/offline без ранее успешных данных;
- удалена.

Для сервера, который хотя бы раз успешно измерялся, переход обратно к пустой
«загрузка…» запрещён backend persistence и frontend merge одновременно.

---

## 6. ADD и DELETE

### Пользователи и область видимости

Пользователи, привязка последнего выбранного пользователя к IP клиента и
конфигурации серверов хранятся в SQLite `data/users.sqlite3`. При первом входе
или после нажатия кнопки «Пользователь» вводится имя. Для обычного имени пароль
не нужен. На первом экране присутствует только поле имени. Если введено имя
`admin`, интерфейс переходит на отдельный экран пароля с кнопкой «Назад»;
ожидается `GPU_MONITOR_ADMIN_PASSWORD` (локальное значение по умолчанию — `0000`).

`aicenter<number>`, `aicenteritl` и `h200` помечаются как основные (`core`) и
видны всем. Добавленный администратором сервер имеет область `shared` и также
виден всем. Сервер обычного пользователя имеет область `private`, виден только
владельцу и удаляется только владельцем. Администратор может удалить любой
сервер, включая основной. Эти разрешения проверяет backend, а не только UI.

`host_paths.json` сохраняется как runtime-зеркало для существующего SSH probe;
источником ownership и visibility является SQLite.

Администратор также видит пункт «Переименовать» в меню карточки. Он изменяет
только `hosts.display_name` в SQLite. Технический hostname, SSH target, ключи
scheduler/cache/history и URL API не меняются. Обычным пользователям операция
недоступна и в UI, и через backend `PATCH /api/hosts/{hostname}`.

### ADD

- карточка создаётся сразу;
- запускается probe только нового хоста;
- полный обход не запускается;
- остальные метрики не очищаются;
- задача разделяет общий semaphore и SSH hard limit;
- hostname, IP и `ssh_target` валидируются;
- option-like SSH target и shell whitespace запрещены.

### DELETE

- карточка удаляется сразу;
- активная asyncio-задача отменяется;
- generation увеличивается;
- поздний sync SSH-result игнорируется;
- persisted last-good snapshot удаляется;
- остальные серверы не затрагиваются.

---

## 7. История метрик

`gpu_metrics_history.py` хранит GPU history в SQLite:

```text
data/metrics/gpu_history.db
```

Collector раз в пять минут читает только успешные записи `_host_cache` и
вызывает `record_batch`. Retention — 14 дней; старые строки удаляются через
`cleanup_old`.

Автоматизированный тест создаёт временную реальную SQLite DB, записывает batch
и читает его обратно. Это проверка фактического persistence, а не mock вызова.

---

## 8. API и безопасность

Read-only endpoints:

- `GET /api/metrics`;
- `GET /api/gpu-history`;
- `GET /api/network/public-ip`;
- `GET /api/network/mesh-health`;
- `GET /api/mesh/watcher-status`;
- `GET /api/vpn/status`;
- `GET /api/quotas`.

Чувствительные или управляющие endpoints защищены `require_admin`:

- ADD/DELETE hosts;
- route protection и запуск watcher;
- proxy start/stop;
- quotas config/forced refresh;
- список SSH aliases;
- remote/local file browser;
- открытие Cursor и SDK agent operations;
- список пользователей и изменение user settings;
- SSH diagnostics.

Loopback считается доверенным. Удалённый клиент должен передать:

```text
Authorization: Bearer <GPU_MONITOR_ADMIN_TOKEN>
```

или:

```text
X-Admin-Token: <GPU_MONITOR_ADMIN_TOKEN>
```

Переменная задаётся в `.env`. Для общего лабораторного развёртывания этого
минимального viewer/admin барьера недостаточно: нужен TLS reverse proxy с
пользовательской аутентификацией. Прямое размещение приложения в интернете
запрещено.

Файловые пути проверяются, SSH вызывается argument list без `shell=True`, а
секреты `.env`, SSH keys и tokens не передаются во frontend.

---

## 9. VPN, overlays и Mesh Route Watcher

GPU Profiler не запускает и не останавливает AmneziaVPN, NetBird и ZeroTier.
Их UI-состояния read-only. ChatGPT/Yandex selective proxy — отдельная
управляемая функция.

Mesh Route Watcher работает вне backend как Windows Scheduled Task
`GPUProfiler-MeshRouteWatcher`. Он:

- удаляет только подтверждённые физические Wi-Fi/LAN hijack routes;
- сохраняет NetBird/ZeroTier/OpenVPN пути;
- обновляет прямые VK/Yandex/OpenVPN endpoint routes на LAN gateway;
- не зависит от порта GPU Profiler;
- пишет heartbeat в `runtime/mesh_route_watcher_status.json`.

Критические правила:

- никогда не удалять и не отключать Amnezia `Block Internet`/kill-switch WFP;
- никогда не создавать fake WFP permits;
- никогда не писать внутренний Qt REG_BINARY `ExceptSites` вручную;
- настройка `ExceptSites` выполняется только в официальном Amnezia UI;
- перед route mutation должен существовать подтверждённый good overlay path;
- успех проверяется реальным TCP/SSH и source address, а не только
  `Get-NetRoute`.

Полная топология, адреса overlays, watcher triggers и post-mortem инцидентов
содержатся в `AGENTS.md`; история изменений консолидирована в этом файле.

---

## 10. Выполненный аудит: найденные проблемы

| Проблема | Причина | Исправление |
|---|---|---|
| Метрики исчезали при SSH timeout | Error result заменял весь host payload | Merge состояния с сохранением last-good |
| Иногда снова появлялась «загрузка…» | Хост пропадал из RAM cache во время sweep/restart | Disk last-good cache + frontend retention |
| HTTP запускал remote refresh | Возраст общего cache проверялся в endpoint | Независимый scheduler, HTTP cache-only |
| Браузеры умножали local/ZT subprocess | Проверки выполнялись в endpoint | Перенесены в scheduler и кэшируются |
| DELETE→ADD принимал старый result | Guard проверял только имя | Host generation identity |
| ADD/DELETE могли потерять JSON update | Нет lock и atomic write | `_config_lock` + atomic replace |
| Route threads обходили semaphore | Executor до 8 на каждый host | Общий hard SSH limit 3 |
| Route fan-out выполнялся слишком часто | Каждый metrics probe проверял все пути | Fan-out только после failure, cache 300с |
| FS/agent/VPN SSH не учитывались | Независимые subprocess call sites | Все подключены к `ssh_runtime` |
| Не было SSH telemetry | Нет единого runtime | Diagnostics API и structured counters |
| Старый HTTP-response перезаписывал новый | Пересекающиеся polling requests | Один in-flight + abort + sequence guard |
| ADD/DELETE конфликтовал с pending HTTP | `_tickSeq` не знал о mutations | `_serverMutationVersion` |
| Grid полностью пересоздавался | `innerHTML` каждый tick | Keyed card updates |
| Startup tasks утекали | Fire-and-forget create_task | Lifespan registry + cancel/gather |
| Admin API был открыт | HTTP auth отсутствовал | Loopback trust + remote token |
| ADD допускал опасный SSH target | Слабая input validation | Строгий pattern без shell/options |
| Новая карточка иногда исчезала и возвращалась | Старый aggregate snapshot не содержал только что добавленный host | Mutation overlay до подтверждения backend |
| Меню шестерёнки прыгало наверх страницы | Polling заменял/перемещал focused DOM-card | Stable keyed DOM, запрет замены открытой карточки, восстановление scroll |
| `Connection timed out` заменялся на «загрузка…» | Placeholder имел тот же приоритет, что конкретная ошибка | Приоритет success → concrete error → loading и per-host error persistence |
| Серверы разных пользователей смешивались | Единственный глобальный JSON inventory | SQLite ownership и server visibility `core/shared/private` |
| Обычный пользователь мог удалить общий host | Удаление защищалось только общим admin guard | Backend ACL: owner удаляет private, admin удаляет любой |
| Поле пароля admin было видно сразу | CSS `label { display:grid }` перебивал HTML `hidden` | `.user-login-fields [hidden] { display:none!important }` и отдельный второй шаг |
| Чистое Linux-развёртывание падало при import | `sdk_agent.py` использовал незаявленный `cursor-sdk` | `cursor-sdk>=1.0.27` добавлен в `requirements.txt` |
| Переименование могло бы сломать SSH/cache identity | UI раньше показывал только технический hostname | Отдельное SQLite-поле `display_name`, admin-only PATCH |

---

## 11. Тестирование и измерения

Запуск:

```powershell
python -m pytest -q
node --check static/app.js
python -m py_compile app.py host_paths.py remote_browse.py sdk_agent.py ssh_runtime.py
```

Результат последнего запуска:

```text
31 passed
JavaScript syntax: OK
Python syntax: OK
FastAPI lifespan smoke: HTTP 200
Background tasks after shutdown: 0
```

Покрытые сценарии:

1. success → timeout → last-good payload сохранён;
2. ADD во время обхода;
3. DELETE во время probe;
4. DELETE→ADD с тем же именем;
5. несколько HTTP clients не создают SSH;
6. медленный host не блокирует быстрый;
7. ADD и regular probes разделяют limit;
8. route diagnostics соблюдает hard cap;
9. frontend не накапливает HTTP requests;
10. ADD/DELETE защищены от старого response;
11. reload страницы читает backend cache;
12. SQLite history действительно записывает строки;
13. duplicate startup не создаёт collectors;
14. remote viewer не может вызвать admin operation;
15. persisted last-good переживает backend restart;
16. frontend не заменяет известные метрики placeholder-ом.
17. IP сохраняет выбранного пользователя;
18. admin принимает только настроенный пароль;
19. private host виден владельцу, shared/core видны всем;
20. ACL удаления запрещает пользователю чужие и основные серверы;
21. поле пароля скрыто на первом шаге независимо от CSS каскада.

Локальный cache-path benchmark для 8 hosts и 100 конкурентных вызовов функции
`/api/metrics`:

```text
1.01 ms total
0.010 ms/request
0 SSH calls
```

Это измерение Python cache path без HTTP transport. Сопоставимого сохранённого
baseline до изменений не существовало, поэтому ускорение «в X раз» не
заявляется.

Реальные лабораторные серверы не использовались для нагрузочных тестов.

---

## 12. Изменённые компоненты

- `app.py` — scheduler, host state, generations, persistence, lifecycle,
  admin guard, IP-bound sessions, visibility ACL, cache-only metrics API.
- `user_config.py` — SQLite users, IP bindings, ownership и области
  `core/shared/private`.
- `ssh_runtime.py` — общий SSH limiter и telemetry.
- `host_paths.py` — ограниченная и кэшируемая route diagnostics.
- `remote_browse.py` — общий limiter, kill/wait при timeout/cancel.
- `sdk_agent.py` — общий limiter для agent SSH tools.
- `static/app.js` — serial polling, mutation guard, last-good retention,
  keyed rendering, user switch и двухшаговый admin login.
- `static/index.html` — `app.js?v=22`, user modal и строгий `[hidden]`.
- `test_audit_architecture.py`, `test_per_host_cache.py`,
  `test_user_config.py` — regression и integration tests.
- `requirements.txt` — полный runtime dependency set, включая `cursor-sdk`.
- `Dockerfile`, `compose.yaml`, `scripts/ubuntu/docker-deploy.sh` — Ubuntu deployment.
- `.env.example` — scheduler, backoff, SSH limit, admin token/password.
- `AGENTS.md` — обязательные архитектурные и safety invariants.
- `README.md` — канонический Ubuntu pipeline установки и обновления.

---

## 13. Оставшиеся ограничения

1. Поддерживается только один backend worker.
2. IP binding удобен для локальной сети, но несколько людей за одним NAT/IP
   разделят выбранного пользователя; это не полноценная web-аутентификация.
3. Sync `ssh.exe`, уже запущенный через `asyncio.to_thread`, живёт до своего
   subprocess timeout даже после отмены asyncio task; late result безопасно
   отбрасывается поколением.
4. После первого развёртывания на совершенно новой машине у сервера без
   единого успешного измерения объективно нет данных для отображения. После
   первого успеха пустая «загрузка…» больше не должна появляться.
5. Визуальный browser screenshot regression не входит в автоматические тесты.
6. Текущий процесс на `0.0.0.0:8000`, обнаруженный во время диагностики, был
   запущен отдельно от канонического `scripts/start.ps1`. Статические файлы
   `v23` он читает с диска сразу, но новая backend persistence активируется
   только после безопасного перезапуска этого конкретного deployment.

---

## 14. Развёртывание на `fic_comp` (20.09.2026)

Хост: Ubuntu 24.04.4 LTS, SSH alias `fic_comp`, пользователь `gregory`.
Приложение развёрнуто в `/home/gregory/gpu_profiler`. Изначальный deployment
через `.venv` и systemd user unit впоследствии заменён Docker-контейнером;
приложение по-прежнему слушает `0.0.0.0:8000`. Проверены:

- systemd state `active`;
- `GET /` — HTTP 200, title `GPU Fleet`;
- `GET /api/session` — HTTP 200;
- login тестового пользователя — HTTP 200;
- `GET /api/metrics` после login — HTTP 200, 9 server entries;
- доступ с Windows по ZeroTier — HTTP 200.

Рабочая ссылка до завершения NetBird:

```text
http://192.168.194.193:8000/
```

NetBird запускался официальным Docker client с `host` networking,
`/dev/net/tun` и capabilities `NET_ADMIN`, `SYS_ADMIN`, `SYS_RESOURCE`.
Management URL существующей сети определён как `https://nettouse.ru:443`.
Публичный cloud отвергал ключ, как и ожидалось для self-hosted key; при работе
с правильным management URL HTTPS доступен, но gRPC/TLS handshake завершается
`DeadlineExceeded`. Проверены client `0.79.0` и версия `0.76.2`, совпадающая с
рабочим Windows peer. Незарегистрированные контейнер и volume удалены, setup
key не сохранён. Для завершения нужен доступный gRPC endpoint/исправление
policy на `nettouse.ru:443` либо новый подтверждённый setup key после этого.

---

## 15. Очистка репозитория и Ubuntu pipeline (20.09.2026)

На этом этапе каноническим production-запуском были Python `.venv` и
пользовательский systemd unit. Этот вариант затем заменён Docker deployment,
описанным ниже.

Из Git удалены runtime-артефакты и воспроизводимые бинарники (`users.db`,
скриншот, собранные launcher EXE), устаревшие compatibility wrappers и
дублирующие audit/history Markdown. Исходники launchers и build script оставлены.
Полный порядок установки, обновления, управления и проверки опубликован в
`README.md`.

---

## 16. Быстрая проверка

```powershell
cd C:\Grisha\mipt\asp\NIR\servers\gpu_monitor

# Tests
python -m pytest -q

# Start canonical local deployment
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1

# Read-only state
Invoke-RestMethod http://127.0.0.1:8765/api/metrics

# Local admin SSH telemetry
Invoke-RestMethod http://127.0.0.1:8765/api/diagnostics/ssh

# Stop only this backend
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
```

---

## 17. Миграция Ubuntu deployment на Docker (20.09.2026)

Добавлены `Dockerfile`, `compose.yaml` и
`scripts/ubuntu/docker-deploy.sh`. Контейнер запускается с host networking для
доступа к NetBird/ZeroTier/OpenVPN маршрутам. `data/`, `logs/`, `runtime/`,
`~/.ssh` подключаются с хоста; SSH mount работает только на чтение. Канонический
список серверов сохраняется в SQLite, а runtime `host_paths.json` восстанавливается
из него на startup. Старые Ubuntu `.venv` install/update scripts и systemd unit
удалены, чтобы в репозитории оставался один production pipeline.

---

## 18. Учёт использования и admin analytics (20.09.2026)

В `data/users/users.db` сохраняются реальные выбранные пользователи и их
сессии: начало, последняя активность, завершение, причина завершения и
длительность. Смена пользователя и закрытие страницы завершают сессию явно;
потерянная вкладка считается вышедшей после двух минут без активности.

Отдельный контейнер `gpu-profiler-debug` слушает порт `8001` и показывает
число пользователей, активных сейчас, среднее число уникальных пользователей
в день/месяц и таблицу по каждому пользователю. CSV содержит все сессии.
Интерфейс и API статистики доступны только после входа `admin` с паролем
`GPU_MONITOR_ADMIN_PASSWORD`. Основной контейнер остаётся на порту `8000`;
оба порта доступны по IP сервера без выдачи SSH-доступа.
