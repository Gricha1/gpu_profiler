# GPU Profiler

```bash
docker compose up -d --build
```

The image build uses the host network so it can reach a DNS resolver provided
by NetBird when that resolver is available only on the host overlay.
Compose prepares ownership of its mutable volumes before starting the
non-root application process, including when it is invoked by `root`.

- приложение: `http://<SERVER_IP>:8000/`
- Developer UI (optional): `docker compose --profile debug up -d`, then
  `http://<SERVER_IP>:8001/`. It is deliberately excluded from the normal
  deployment to reduce ControllerServer memory use.

## Доступ к серверам GPU

Нужны одна сеть NetBird, SSH-доступ по ключу к GPU-серверам и заполненный
`~/.ssh/config` на сервере, где запускается GPU Profiler. При Docker-запуске
от `root` это `/root/.ssh/config`; публичный ключ этого сервера должен быть
добавлен в `~/.ssh/authorized_keys` выбранного пользователя на каждом
GPU-сервере.

Пример для Ubuntu: `~/.ssh/config`.

```sshconfig
Host aicenter2
  HostName 100.98.59.202
  Port 22
  User test_user
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 60

Host aicenter3
  HostName 100.98.241.137
  Port 22
  User test_user
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 60
```

`Host` — произвольное имя, `HostName` — NetBird IP, `User` — пользователь на
GPU-сервере, `IdentityFile` — приватный ключ для подключения. Для Windows
используйте тот же пример в `C:\Users\<user>\.ssh\config` и путь вида
`C:/Users/<user>/.ssh/id_ed25519`.

```bash
ssh aicenter2 'nvidia-smi -L'
```

Затем в GPU Fleet выберите этот алиас в «Добавить сервер».
