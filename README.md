# DeepRhetor

DeepRhetor is a local research-and-writing application. Given a descriptive
prompt, it plans research, discovers and archives sources, builds a grounded
claim inventory with exact evidence, and produces a polished cited LaTeX report.

**Status:** Stage 1 foundation complete. See [PLAN.md](PLAN.md) for
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

## Quick start (foundation)

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
pytest
```

## Package layout

```
src/deeprhetor/
  config/         User config loader + redaction
  domain/         Versioned Pydantic models
  db/             Async SQLite engine helpers
  repositories/   Typed repository stubs
  plugins/        Search/source plugins (Stage 3+)
  models/         Model registry / agents (Stage 4+)
  workflow/       LangGraph scheduler (Stage 5+)
  services/       Deterministic application services
  web/            FastAPI UI (Stage 9+)
```

## License

MIT
