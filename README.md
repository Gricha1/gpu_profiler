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

Чтобы карточка удалённого сервера показывала метрики, ControllerServer должен
видеть этот сервер в той же сети NetBird и иметь обычный SSH-доступ к нему.
GPU Profiler не создаёт учётные записи и не подключает NetBird автоматически.

1. Подключите ControllerServer и GPU-сервер к одной сети NetBird. Проверьте
   маршрут и SSH с ControllerServer, например: `ssh aicenter2 nvidia-smi -L`.
2. На GPU-сервере администратор должен разрешить нужному пользователю вход по
   ключу: добавить публичный ключ ControllerServer в
   `~/.ssh/authorized_keys` этого пользователя. Приватный ключ на удалённый
   сервер не копируется.
3. На машине, где запускается GPU Profiler, заполните `~/.ssh/config`.
   При Docker-развёртывании командой от `root` это `/root/.ssh/config`: Compose
   монтирует этот каталог в приложение только для чтения.

Пример для Windows: `C:\Users\<ваш_пользователь>\.ssh\config`.

```sshconfig
Host aicenter2
  HostName 100.98.59.202
  Port 22
  User test_user
  IdentityFile C:/Users/User/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 60

Host aicenter3
  HostName 100.98.241.137
  Port 22
  User test_user
  IdentityFile C:/Users/User/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 60
```

`Host` — произвольный короткий псевдоним, который будет виден в списке
«SSH хосты» при добавлении сервера. `HostName` — NetBird IP или DNS-имя,
`User` — учётная запись на GPU-сервере, а `IdentityFile` — путь к её
приватному ключу на ControllerServer. После сохранения конфига проверьте:

```bash
ssh aicenter2 'nvidia-smi -L'
```

Затем в GPU Fleet нажмите «Добавить сервер» и выберите алиас из блока
«SSH хосты из ~/.ssh/config». Приложение использует именно выбранный алиас,
поэтому применяются его `User`, ключ и остальные SSH-параметры.
