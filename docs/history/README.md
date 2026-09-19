# docs/history/

Snapshot reports from past incidents, migrations, and test runs. **Do not edit
historical entries to retroactively change the record** — add a new entry
instead.

These documents were superseded by the canonical [AGENTS.md](../AGENTS.md) on
the documentation consolidation. They remain available as evidence and
context for the safety invariants that AGENTS.md currently enforces.

| File | Date | What it covers |
|------|------|----------------|
| [MESH_VK_REPORT.md](./MESH_VK_REPORT.md)        | 2026-09-05 | Amnezia kill-switch (WFP) blocking `100.98.59.202` / `10.43.71.7`; resolution via `VpnAllExceptSites` and Disconnect→Connect in the Amnezia UI. The script `configure_split_tunnel.ps1` it describes as registry-writing has since been changed to a **read-only** helper — see AGENTS.md §9.4 and §11. |
| [MIGRATION_REPORT.md](./MIGRATION_REPORT.md)    | 2026-09-05 | Migration of the project to the canonical `servers/gpu_monitor/` layout with PID-safe ChatGPT proxy, public-IP endpoint, and Amnezia / Full-TUN / NetBird endpoints disabled in the UI. |
| [TEST_REPORT.md](./TEST_REPORT.md)              | 2026-09-05 | Pre-consolidation smoke-test results (UI, single-instance, metrics, proxy, watcher tasks, etc.). |
