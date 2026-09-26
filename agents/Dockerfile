FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /agent
COPY remote_probe.py /agent/remote_probe.py
COPY agents/gpu_profiler_agent.py /agent/agents/gpu_profiler_agent.py
CMD ["python", "/agent/agents/gpu_profiler_agent.py"]
