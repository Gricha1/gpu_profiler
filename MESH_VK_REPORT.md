# MESH / VK / AMNEZIA — отчёт по предыдущей задаче

Дата: 2026-09-05  
Проект: `C:\Grisha\mipt\asp\NIR\servers\gpu_monitor`

---

## 1. Проблема

```
ssh aicenter2
→ 100.98.59.202:22
→ ssh: connect to host 100.98.59.202 port 22: Permission denied
```

Это **не** ошибка SSH-ключа и **не** ChatGPT selective proxy.

---

## 2. Точная причина (доказано)

| Проверка | Результат |
|----------|-----------|
| `Find-NetRoute 100.98.59.202` | **через `wt0` (NetBird)** — маршрут правильный |
| `ping 100.98.59.202` | **General failure** |
| `ssh -vvv` | `finish_connect ... error: 10013` = **WSAEACCES** |
| AmneziaWG `pcawg` | Stopped |
| Amnezia Premium | **активен через `tun2` / tun2socks** |
| WFP (Amnezia sublayer) | PERMIT только на VPN-адаптер `tun2` + редкие `Allow Exclude route`; иначе **Block Internet 0.0.0.0/0** на `ALE_AUTH_CONNECT_V4` |

**Вывод:** Windows routing шлёт пакеты в NetBird (`wt0`), но **Amnezia kill-switch (WFP)** запрещает CONNECT ко всему, что не VPN-адаптер и не в списке exclude. Поэтому metric/route-костыли **не лечат** WSAEACCES.

То же для всей сети `100.98.0.0/16` и ZeroTier `10.43.71.0/24` (в логах Amnezia уже были `Timeout connecting to 10.43.71.140`).

---

## 3. Состояние Amnezia split-tunnel до фикса

В реестре:

- `Conf/routeMode = 1` (`VpnOnlyForwardSites`)
- `Conf/sitesSplitTunnelingEnabled` — **не задан** → default **false**
- списка `ExceptSites` **не было**

Итог: full tunnel + kill-switch, mesh не исключён.

Штатный механизм Amnezia (подтверждён исходниками / issues):

- `sitesSplitTunnelingEnabled = true`
- `routeMode = 2` (`VpnAllExceptSites`)
- адреса/домены в `Conf/ExceptSites`
- **применяется только после Disconnect → Connect** (WFP rebuild)

---

## 4. Что сделано (постоянная конфигурация)

### 4.1. Amnezia ExceptSites (реестр)

Скрипт:

`scripts/amnezia/configure_split_tunnel.ps1`

Список:

`config/amnezia_except_sites.txt`

Записано в:

`HKCU\Software\AmneziaVPN.ORG\AmneziaVPN\Conf`

| Ключ | Значение |
|------|----------|
| `sitesSplitTunnelingEnabled` | **1** |
| `routeMode` | **2** (`VpnAllExceptSites`) |
| `ExceptSites` | см. ниже |

**ExceptSites (mesh):**

- `100.98.0.0/16` — NetBird  
- `10.43.71.0/24` — ZeroTier  
- `192.168.194.0/24` — ZT home  
- `172.24.0.0/16` — ZT 172.24  

**ExceptSites (VK, domain-based):**

- `vk.com`, `www.vk.com`, `m.vk.com`
- `api.vk.com`, `login.vk.com`, `id.vk.com`
- `userapi.com`, `vk-cdn.net`, `vkuservideo.net`, `queuev4.vk.com`

Backup реестра:

`logs/amnezia_conf_backup_YYYYMMDD_HHMMSS.reg`

### 4.2. Почему это не ломает Full VPN

Режим **«VPN для всего, кроме списка»**:

- обычный интернет → Amnezia Full VPN  
- mesh CIDR + VK → direct / exclude (WFP `Allow Exclude route`)  
- ChatGPT selective proxy (`:10808` + PAC) — отдельная система, не трогалась  

### 4.3. GPU Profiler (диагностика, без route-rewrite)

Добавлено / изменено:

| Файл | Назначение |
|------|------------|
| `services/mesh_health.py` | TCP probe NetBird/ZT → OK / BLOCKED / DOWN |
| `GET /api/network/mesh-health` | API health-check |
| UI Network card | pills NetBird / ZeroTier |
| startup | **убран** авто-`apply_protected_routes` (route-костыли не лечат WFP) |

Profiler **не** стартует/стопит Amnezia.

---

## 5. Что ещё НЕ завершено (критично)

На момент отчёта после записи реестра:

- `tun2` всё ещё **Up**
- TCP к `100.98.59.202:22` / `10.43.71.7:22` — **FAIL**
- значит WFP exclude **ещё не пересобран**

**Обязательный шаг пользователя (один раз):**

1. Открыть **AmneziaVPN** (`Desktop\AmneziaVPN.lnk`)  
2. **Disconnect**  
3. Проверить Settings → раздельное туннелирование сайтов: **Вкл**, режим **все кроме списка**, в списке mesh + VK  
4. **Connect** снова  
5. Проверить:
   ```bat
   ssh aicenter2
   ping 100.98.59.202
   ```
6. В WFP после Connect должны появиться `Allow Exclude route` для `100.98.0.0/16` (и др.)

Без Disconnect/Connect Amnezia **не применяет** ExceptSites к kill-switch — это штатное поведение клиента, не «лечение рестартом NetBird».

Также: backend GPU Profiler нужно **перезапустить**, чтобы подтянуть `/api/network/mesh-health` (сейчас мог отвечать 404 со старым процессом).

---

## 6. Тест-матрица (план / статус)

| Сценарий | Amnezia | Proxy | Ожидание | Статус |
|----------|---------|-------|----------|--------|
| aicenter2 | OFF | OFF | SSH OK | **нужен Disconnect для замера OFF** |
| aicenter2 | ON | OFF | SSH OK (после Connect с ExceptSites) | **ожидает reconnect** |
| aicenter2 | OFF | ON | SSH OK | ожидает |
| aicenter2 | ON | ON | SSH OK | ожидает |
| Route 100.98 | any | any | через `wt0` | **PASS** (уже так) |
| VK при Amnezia ON | ON | — | direct ISP | ожидает Connect + проверку |
| Обычный сайт при Amnezia ON | ON | — | VPN IP | ожидает |
| ChatGPT Proxy | — | ON | PAC → :10808 | не ломался (отдельная система) |

---

## 7. Чего намеренно НЕ делали

- Слепые `route add/remove` / metric-хаки как «лечение» WSAEACCES  
- `netbird down/up` как решение  
- Убийство всех `sing-box` / правка `~/.ssh/config`  
- Управление Amnezia из GPU Profiler UI  
- Удаление Amnezia / смена её сервера вручную без split-tunnel  

---

## 8. Краткий вердикт

| Вопрос | Ответ |
|--------|--------|
| Что блокировало `100.98.59.202`? | Amnezia kill-switch **WFP** (`Block Internet`), не маршрут |
| Уровень | WFP / Amnezia policy (не SSH auth, не NetBird route) |
| Постоянное изменение | Amnezia `VpnAllExceptSites` + ExceptSites (mesh CIDR + VK domains) в реестре |
| Почему Full VPN жив | Исключения только для списка; остальной трафик в VPN |
| Как VK исключён | Domain-based ExceptSites в Amnezia |
| Что осталось сделать | **Disconnect → Connect** в Amnezia + перезапуск GPU Profiler + прогон тест-матрицы |

---

## 9. Команды для проверки после Connect

```bat
ssh -vvv aicenter2
powershell -NoProfile -Command "Find-NetRoute -RemoteIPAddress 100.98.59.202 | Format-List"
powershell -NoProfile -Command "Test-NetConnection 100.98.59.202 -Port 22"
curl https://ifconfig.me/ip
ssh aicenter2
```

UI: http://127.0.0.1:8765/ → Network → NetBird / ZeroTier pills + Public IP.
