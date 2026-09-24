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
