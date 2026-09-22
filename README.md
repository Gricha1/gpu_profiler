# GPU Profiler

```bash
docker compose up -d --build
```

The image build uses the host network so it can reach a DNS resolver provided
by NetBird when that resolver is available only on the host overlay.

- приложение: `http://<SERVER_IP>:8000/`
- Developer UI: `http://<SERVER_IP>:8001/`
