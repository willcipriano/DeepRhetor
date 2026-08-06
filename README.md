# DeepRhetor

DeepRhetor is a local research-and-writing application. Given a descriptive
prompt, it plans research, discovers and archives sources, builds a grounded
claim inventory with exact evidence, and produces a polished cited LaTeX report.

**Status:** MVP `0.1.0` (Stages 0–10 complete). Report quality is evaluated
manually; see [docs/MANUAL_ACCEPTANCE.md](docs/MANUAL_ACCEPTANCE.md). Automated
integrity coverage lives in `tests/test_integrity.py` and the stage test
modules. Architecture and deferred work: [PLAN.md](PLAN.md).

## Locked stack

| Layer | Choice |
|-------|--------|
| Language | Python ≥ 3.11 |
| Model calls | **Pydantic AI** (typed outputs, tools, usage) |
| Orchestration | **LangGraph** as a thin workflow scheduler only |
| Agents | No application-level LangChain agents or chat wrappers |
| Persistence | One SQLite file per project |
| UI | FastAPI + Jinja + HTMX (localhost) |

## Cost / capability ladder

| Role | Tier | Job |
|------|------|-----|
| Topic worker | Cheap | Search, triage, scan, propose claims |
| Supervisor / verifier / critic | Mid | Plan, verify, judge coverage |
| Writer | Frontier | Compose prose from approved claims only |

## Quick start

### 1. Install

```bash
# Prefer Python 3.11+ (examples use 3.12)
python -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Optional hard-acquisition extras: `pip install -e ".[playwright]"` then
`playwright install chromium`. OCR uses `pytesseract` plus a system Tesseract
binary when scanned PDFs need it; missing binaries are skipped gracefully.

### 2. User config and API keys

Credentials stay in a **user-level** config file (never in a project database
or the repository). Copy the example:

```bash
# Linux / macOS
mkdir -p ~/.config/deeprhetor
cp config.example.toml ~/.config/deeprhetor/config.toml

# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:APPDATA\deeprhetor" | Out-Null
Copy-Item config.example.toml "$env:APPDATA\deeprhetor\config.toml"
```

Edit that file and set placeholders only in docs/chats — use your own keys locally:

| Provider | Config key | Environment override |
|----------|------------|----------------------|
| OpenRouter | `[providers.openrouter] api_key` | `DEEPRHETOR_OPENROUTER_API_KEY` |
| Tavily | `[providers.tavily] api_key` | `DEEPRHETOR_TAVILY_API_KEY` |

See `config.example.toml` for model presets and safety limits.

### 3. TeX toolchain (PDF export)

Publication emits `.tex` / `.bib` always. PDF compile needs:

- [Pandoc](https://pandoc.org/)
- [Tectonic](https://tectonic-typesetting.github.io/)

If either binary is missing, DeepRhetor skips PDF compile and still keeps the
LaTeX artifacts and provenance manifest.

### 4. Run the local app

```bash
deeprhetor version
deeprhetor serve --port 8765
# Open http://127.0.0.1:8765 (loopback only)
```

CLI project helpers:

```bash
deeprhetor project create --path ./demo.deeprhetor --title Demo --prompt "Explain X"
deeprhetor project backup --path ./demo.deeprhetor --dest ./demo-backup.deeprhetor
```

Project files use a preferred `.deeprhetor` (or `.sqlite`) extension. Each file
is one portable SQLite database with schema, corpus FTS, workflow state, and
artifacts. Backup checkpoints WAL before copying.

### 5. Tests

```bash
# Default suite (no live network providers)
pytest
# or: py -3.12 -m pytest -m "not live"

# Optional live Tavily (requires a real Tavily key in user config or env)
DEEPRHETOR_LIVE_TAVILY=1 py -3.12 -m pytest -m live
```

## Package layout

```
src/deeprhetor/
  config/         User config loader + redaction
  domain/         Versioned Pydantic models
  db/             Async SQLite engine helpers
  repositories/   Typed project / workflow / corpus repositories
  plugins/        Search/source plugins
  models/         Model registry / agents
  workflow/       LangGraph scheduler
  services/       Project store, FTS, recovery, publication, …
  web/            FastAPI UI
```

## License

MIT
