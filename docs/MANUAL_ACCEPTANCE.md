# Manual acceptance guide (MVP)

Report quality is evaluated manually for the MVP. Use this checklist with the
local web UI (`deeprhetor serve`) and a configured OpenRouter + Tavily setup.
Automated mechanical correctness is covered by `pytest` (see
`tests/test_integrity.py`).

For each scenario below, create a **new** project, run through plan approval and
research, then score the deliverables.

## Shared evaluation checklist

Score or note for every completed run:

- [ ] Project created from the local web app; prompt (and optional files) saved
- [ ] Structured research plan received and approved (or regenerated with feedback)
- [ ] Capability-aware research ran across configured sources without Cartesian spam
- [ ] Archived originals available; segment scan reaches a terminal accounting state
- [ ] Claims inspectable with exact quoted evidence (or honest gaps)
- [ ] Stop / relaunch / resume does not lose completed tasks or invent duplicates
- [ ] Final report answers the prompt as fully as the evidence permits (no fluff padding)
- [ ] PDF (or `.tex` if PDF toolchain missing) has footnotes + bibliography
- [ ] Project retains `.tex`, `.bib`, provenance manifest, validation results, and `.deeprhetor` SQLite

Pass / Fail / Notes: _______________

---

## Prompt 1 — Broad general-web topic

**Suggested prompt**

> Explain how modern heat pumps work for residential heating, including efficiency
> trade-offs versus gas furnaces, common misconceptions, and what homeowners in
> cold climates should verify before installing one.

**Focus**

- General-web discovery (Tavily) plus encyclopedic background as available
- Accessible explanations that stay citation-backed

**Scenario checklist**

- [ ] Plan covers mechanism, efficiency, misconceptions, and cold-climate caveats
- [ ] Web sources archived with readable text / segments
- [ ] Claims distinguish mechanism facts from vendor marketing claims
- [ ] Report is readable to a non-specialist without inventing numbers
- [ ] Shared evaluation checklist completed

Pass / Fail / Notes: _______________

---

## Prompt 2 — Scholarly topic

**Suggested prompt**

> Summarize the scholarly debate on the causes of the Late Bronze Age Collapse,
> contrasting systems/collapse models with invasion or migration hypotheses.
> Prefer peer-reviewed and reference works; mark contested points clearly.

**Focus**

- Scholarly providers (OpenAlex / Crossref / arXiv where relevant)
- Careful hedging; no forced certainty

**Scenario checklist**

- [ ] Plan distinguishes competing explanatory families
- [ ] Bibliographic metadata present for scholarly hits used
- [ ] Contested claims labeled; opposing evidence not silently dropped
- [ ] Bibliography looks like a mini literature note, not a single wiki page
- [ ] Shared evaluation checklist completed

Pass / Fail / Notes: _______________

---

## Prompt 3 — Unconventional thesis / indirect historical evidence

**Suggested prompt**

> Defend, as far as the evidence allows, the thesis that popular memory of the
> 1918 influenza pandemic was shaped more by wartime censorship and municipal
> public-health theater than by clinical severity alone. Rely on indirect
> contemporary evidence (newspapers, ordinances, diaries) where direct clinical
> statistics are thin, and explicitly separate inference from primary quotes.

**Focus**

- Indirect / folkloric / inferential evidence relations
- Writer must not launder inference into direct quotation

**Scenario checklist**

- [ ] Plan allows indirect and testimony-class evidence where appropriate
- [ ] Evidence directness / relation types visible on claims (supports/qualifies/…)
- [ ] Exact quotes remain verbatim; inferences are labeled as such in prose
- [ ] Critic/gate does not demand impossible direct clinical primary sources
- [ ] Shared evaluation checklist completed

Pass / Fail / Notes: _______________

---

## Prompt 4 — User-supplied files heavy

**Suggested prompt**

> Using only (or primarily) the documents I upload, analyze their argument
> structure, extract atomic claims with exact evidence spans, note gaps, and
> produce a short cited synthesis. Prefer my files over open-web discovery.

**Setup**

- Upload several local PDFs / Markdown / HTML / DOCX under the project
- Optionally disable or minimize discovery providers if the UI allows; otherwise
  instruct the plan to prioritize uploaded corpus

**Scenario checklist**

- [ ] Local files archived and searchable via FTS
- [ ] Segment scans complete (or failed segments accounted for)
- [ ] Most approved claims cite uploaded documents, not invented web pages
- [ ] Synthesis acknowledges corpus limits instead of hallucinating coverage
- [ ] Shared evaluation checklist completed

Pass / Fail / Notes: _______________

---

## Prompt 5 — Interrupted and resumed run

**Suggested prompt**

> Use Prompt 1 or 2 again (or any mid-sized topic). After plan approval and after
> workers have started scanning or proposing claims, stop the server (Ctrl+C),
> wait a few seconds, then restart `deeprhetor serve` and reopen the same project.

**Focus**

- Interrupted-run recovery UX and ledger correctness

**Scenario checklist**

- [ ] On reopen, interrupted run/tasks are surfaced (recovery screen or status)
- [ ] Resume / retry continues without duplicate worker assignments for the same keys
- [ ] Completed scans, claims, and artifacts from before the kill remain present
- [ ] Publication can still complete after resume
- [ ] Shared evaluation checklist completed

Pass / Fail / Notes: _______________

---

## Sign-off

| Scenario | Pass? | Evaluator | Date |
|----------|-------|-----------|------|
| 1 Broad general-web | | | |
| 2 Scholarly | | | |
| 3 Unconventional / indirect | | | |
| 4 User-supplied files | | | |
| 5 Interrupted resume | | | |

MVP release readiness notes: _______________
