# DeepRhetor

DeepRhetor is a local research-and-writing application. Given a descriptive
prompt, it plans research, discovers and archives sources, builds a grounded
claim inventory with exact evidence, and produces a polished cited LaTeX report.

**Status:** Stages 1–6 complete (through full source stack: Tavily, OpenAlex,
Crossref, arXiv, Playwright fallback, OCR). See [PLAN.md](PLAN.md) for
architecture and remaining build stages.

## Locked stack

| Layer | Choice |
|-------|--------|
| Language | Python ≥ 3.11 |
| Model calls | **Pydantic AI** (typed outputs, tools, usage) |
| Orchestration | **LangGraph** as a thin workflow scheduler only |
| Agents | No application-level LangChain agents or chat wrappers |
| Persistence | One SQLite file per project |
| UI (later) | FastAPI + Jinja + HTMX |

## Cost / capability ladder

| Role | Tier | Job |
|------|------|-----|
| Topic worker | Cheap | Search, triage, scan, propose claims |
| Supervisor / verifier / critic | Mid | Plan, verify, judge coverage |
| Writer | Frontier | Compose prose from approved claims only |

## Quick start

```bash
# Prefer Python 3.11+
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Copy and edit user config (credentials stay outside the repo)
# Linux/macOS: ~/.config/deeprhetor/config.toml
# Windows:     %APPDATA%\deeprhetor\config.toml
cp config.example.toml path/to/your/config.toml

deeprhetor version
deeprhetor project create --path ./demo.deeprhetor --title Demo --prompt "Explain X"
deeprhetor project backup --path ./demo.deeprhetor --dest ./demo-backup.deeprhetor
pytest

# Optional live Tavily (uses %APPDATA%/deeprhetor/config.toml or XDG config)
DEEPRHETOR_LIVE_TAVILY=1 py -3.12 -m pytest -m live
```

Optional extras for hard acquisition: `pip install -e ".[playwright]"` then
`playwright install chromium`. OCR uses `pytesseract` (Python) plus a system
Tesseract binary when scanned PDFs need it; missing binaries are skipped
gracefully.

Project files use a preferred `.deeprhetor` (or `.sqlite`) extension. Each file
is one portable SQLite database with schema, corpus FTS, workflow state, and
artifacts. Backup checkpoints WAL before copying.

## Package layout

```
src/deeprhetor/
  config/         User config loader + redaction
  domain/         Versioned Pydantic models
  db/             Async SQLite engine helpers
  repositories/   Typed project / workflow / corpus repositories
  plugins/        Search/source plugins (Stage 3+)
  models/         Model registry / agents (Stage 4+)
  workflow/       LangGraph scheduler (Stage 5+)
  services/       Project store, FTS, recovery, checkpoints
  web/            FastAPI UI (Stage 9+)
```

## License

MIT
