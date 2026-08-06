# DeepRhetor — tangential ideas backlog

Ideas parked while other work lands on DeepRhetor. Implement later; do not block the active agent.

Status legend: `queued` · `in_progress` · `done` · `dropped`

---

## IDEA-001 — Preferred host fragments should be configurable

- **Status:** queued
- **Captured:** 2026-08-06
- **Where today:** hardcoded tuple `_PREFERRED_HOST_FRAGMENTS` in `src/deeprhetor/workflow/live.py` (~457–464), used by `_prefer_stable_sources` to rank search hits before fetch/archive.
- **Current defaults (order = preference weight):**
  1. `wikipedia.org`
  2. `plato.stanford.edu`
  3. `britannica.com`
  4. `iep.utm.edu`
  5. `arxiv.org`
  6. `openalex.org`
- **Desired:** make the list (and order) configurable — not a module constant.
- **Likely home:** `AppConfig` / `config.toml` (near `[limits]` or a new `[discovery]` / `[sources]` section), with these values as defaults so existing behavior is preserved. Wire through `build_live_agents` / `LiveResearchWorker` so `_prefer_stable_sources` takes the list as an argument (or reads from worker/config).
- **Acceptance sketch:**
  - Defaults match current hardcoded list/order when unset.
  - Config override changes ranking without code edits.
  - Document in `config.example.toml`.
  - Small unit test for ranking with a custom fragment list.

---

## IDEA-002 — Relevance filter must not hardcode domain vocabulary

- **Status:** queued
- **Captured:** 2026-08-06
- **Where today:** `_looks_relevant` in `src/deeprhetor/workflow/live.py` (~504–545) hardcodes Aristotle/formal-logic allow terms (`logic_terms`) and reject titles (`reject_titles`: Politics, Nicomachean Ethics, Poetics, Rhetoric, etc.). Nearby relatives (same smell): `_CORE_TERMS`, and `_keywords_from_plan` biasing toward `aristotle` / `logic` / `syllogism` / `organon`.
- **Problem:** relevance policy is baked into the live worker for one research domain. Any non–formal-logic project inherits wrong rejects/allows, or needs code edits.
- **Desired:** no domain-specific allow/reject lists in `live.py`. Relevance should come from project/plan configuration (or be derived from the research prompt / plan topics), not module constants.
- **Design directions to explore (pick one later):**
  1. **Project-scoped relevance policy** (preferred sketch): e.g. on the project or plan — `must_terms`, `boost_terms`, `reject_title_patterns`, optional “objective unlocks reject” rules (today: Rhetoric allowed if objective mentions rhetoric).
  2. **Prompt/plan-derived:** extract must-terms from the plan objective; only use generic overlap heuristics (already partly present via `_CORE_TERMS` ∩ objective tokens). Drop Aristotle-specific rejects unless supplied by project content.
  3. **Content pack / archetype:** reusable domain packs (logic, biology, …) referenced by project, not hardcoded in workflow.
- **Acceptance sketch:**
  - `live.py` has no Aristotle/logic-specific string lists.
  - A project can supply (or omit) reject/allow terms; omitting does not inject formal-logic defaults.
  - Current Aristotle formal-logic behavior remains achievable via project/config defaults for that project only.
  - Unit tests cover: reject list hit → false; objective unlock exception; generic project with empty policy → no domain rejects.
