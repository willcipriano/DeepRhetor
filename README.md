# DeepRhetor

DeepRhetor turns a descriptive research prompt into a **citation-backed paper**. It plans subtopics, searches pluggable document sources, extracts grounded claims, keeps the research on track, and writes the final prose with a cost-aware model ladder.

**Status: design only — not implemented yet.** See [PLAN.md](PLAN.md) for architecture and build stages.

## Cost ladder

| Lane | Job | Model tier |
|------|-----|------------|
| Retrieve | Search, triage, and filter documents | Cheap |
| Claim | Translate useful docs into lists of cited claims | Mid |
| Write | Compose the actual paper from the claim inventory | Frontier |

## Pipeline sketch

```mermaid
flowchart TD
  prompt[DescriptivePrompt] --> planner[Planner_mid]
  planner --> topics[SubtopicFanout]
  topics --> search[SearchPlugins_cheap]
  search --> docs[DocumentCorpus]
  docs --> claims[ClaimExtractor_mid]
  claims --> track[KeepOnTrackCritic_mid]
  track -->|gaps| topics
  track -->|ok| outline[Outline_mid]
  outline --> writer[PaperWriter_frontier]
  writer --> paper[CitedPaper]
```

## Planned stack

- **Python** with **LangChain** / **LangGraph** for orchestration
- **Pluggable search providers** — bring your own APIs (web, academic, local files); no hard-coded single vendor at the core
- **Provider-agnostic chat models** for cheap / mid / frontier lanes

## License

MIT
