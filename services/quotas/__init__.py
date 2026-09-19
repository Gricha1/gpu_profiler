"""AI Coding Quotas — collector layer.

This package polls three coding-agent providers and exposes normalized
snapshots for the GPU Profiler UI:

- **Kimi**: queries the local `kimi web` server (`/api/v1/oauth/usage`)
  using the bearer token written to `~/.kimi-code/server.token`. If the
  local server is not running, status is reported as `cli_not_found` /
  `not_logged_in`.
- **MiniMax**: hits the MiniMax billing usage endpoint
  (`https://api.minimax.io/v1/billing/usage?group_id=...`) using a
  `sk-cp-…` Token Plan key from `.env` (`MINIMAX_API_KEY`,
  `MINIMAX_GROUP_ID`). The endpoint reference is taken from the open-source
  Win-CodexBar (MIT) implementation; if it returns 404 / 401 the collector
  reports `api_unavailable` and does **not** fabricate data.
- **Codex**: invokes `codex app-server` over stdio JSON-RPC
  (`account/rateLimits/read`) when the Codex CLI is installed and
  reachable. Falls back to `cli_not_found` otherwise. No OpenAI API key
  is required and no ChatGPT web page is scraped.

Secrets handling
----------------
- The MiniMax API key is loaded from `.env` (gitignored). It is **never**
  returned in API responses, written to logs, or echoed in error messages.
  The `.env.example` template carries only placeholder values.
- The Kimi local-server bearer token is read from a per-user file
  (`server.token`) and is **not** persisted by GPU Profiler.
- Codex auth is whatever the locally-installed Codex CLI has stored
  (`~/.codex/auth.json`); this collector never copies or stores it.

Public surface
--------------
- :func:`get_snapshot` — returns the most recent snapshot (cached,
  refreshed in the background).
- :func:`refresh_now` — forces a refresh and returns the new snapshot.
- :func:`set_minimax_config` — runtime API-key / group_id setter used by
  the in-UI settings (writes to `.env` so the change survives restart).
"""
