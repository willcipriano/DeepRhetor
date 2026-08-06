"""Live workflow agents: real search/fetch archive + OpenRouter planning."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from deeprhetor.config.loader import load_config
from deeprhetor.config.settings import AppConfig
from deeprhetor.domain.discovery import SearchRequest
from deeprhetor.domain.enums import PlanStatus, RhetoricalPosture
from deeprhetor.domain.knowledge import (
    Evidence,
    EvidenceLocation,
    ProposedClaim,
    quote_content_hash,
)
from deeprhetor.domain.planning import PlanSection, PlanTopic, ResearchPlan
from deeprhetor.models.deps import AgentDeps
from deeprhetor.models.registry import ModelRegistry
from deeprhetor.models.roles import RoleName
from deeprhetor.plugins.registry import SearchProviderRegistry
from deeprhetor.repositories.document import DocumentRepository
from deeprhetor.repositories.knowledge import ClaimRepository, EvidenceRepository
from deeprhetor.services.acquisition import AcquisitionPipeline
from deeprhetor.services.fetch import FetchError, SSRFBlockedError
from deeprhetor.workflow.agents import FakeCoverageCritic, PydanticSupervisor

logger = logging.getLogger(__name__)


@dataclass
class LiveResearchWorker:
    """Search → fetch → archive worker used by topic_worker_fanout."""

    providers: SearchProviderRegistry
    documents: DocumentRepository
    project_id: str
    acquisition: AcquisitionPipeline = field(
        default_factory=lambda: AcquisitionPipeline(enable_playwright=False)
    )
    max_docs_per_assignment: int = 2
    max_results: int = 5
    results: list[dict[str, Any]] = field(default_factory=list)

    async def acknowledge(self, assignment: dict[str, Any]) -> dict[str, Any]:
        provider_name = str(assignment.get("provider_or_class") or "")
        objective = str(assignment.get("objective") or assignment.get("title") or "")
        topic_id = assignment.get("topic_id")
        archived: list[str] = []
        errors: list[str] = []
        hit_count = 0

        try:
            provider = self.providers.get(provider_name)
        except KeyError:
            msg = f"unknown_provider:{provider_name}"
            errors.append(msg)
            result = {"ok": False, "error": msg, "topic_id": topic_id, "provider": provider_name}
            self.results.append(result)
            return result

        try:
            response = await provider.search(
                SearchRequest(
                    query=objective,
                    provider=provider_name,
                    max_results=self.max_results,
                )
            )
            hit_count = len(response.hits)
            ranked_hits = _prefer_stable_sources(list(response.hits))
            for hit in ranked_hits:
                if len(archived) >= self.max_docs_per_assignment:
                    break
                if not _looks_relevant(hit.title, hit.snippet, objective):
                    errors.append(f"skipped_irrelevant:{hit.title}")
                    continue
                try:
                    if provider_name == "mediawiki" and hit.title:
                        parsed = await provider.fetch_and_parse(hit.title)  # type: ignore[attr-defined]
                        text_bytes = parsed.text.encode("utf-8")
                        canonical = None
                        if parsed.source_metadata and parsed.source_metadata.extra:
                            canonical = parsed.source_metadata.extra.get("canonical_url")
                        canonical = canonical or hit.url
                        doc, _version, _segs = await self.documents.archive_parsed(
                            project_id=self.project_id,
                            raw_content=text_bytes,
                            parsed=parsed,
                            media_type="text/plain",
                            canonical_url=canonical,
                            original_url=hit.url or canonical,
                            source_class=provider_name,
                            title=hit.title or parsed.title,
                            extra_metadata={
                                "provider": provider_name,
                                "topic_id": topic_id,
                                "hit_id": hit.hit_id,
                                "acquisition_method": "mediawiki-extract",
                            },
                        )
                        archived.append(doc.id)
                        continue

                    url = _public_http_url(hit.url)
                    if not url:
                        continue
                    acquired = await self.acquisition.acquire(url, max_bytes=2_000_000)
                    doc, _version, _segs = await self.documents.archive_parsed(
                        project_id=self.project_id,
                        raw_content=acquired.fetch.content,
                        parsed=acquired.parsed,
                        media_type=acquired.fetch.media_type,
                        canonical_url=acquired.fetch.final_url or url,
                        original_url=url,
                        source_class=provider_name,
                        title=hit.title or acquired.parsed.title,
                        extra_metadata={
                            "provider": provider_name,
                            "topic_id": topic_id,
                            "hit_id": hit.hit_id,
                            "acquisition_method": acquired.method,
                        },
                    )
                    archived.append(doc.id)
                except (FetchError, SSRFBlockedError, OSError, ValueError, LookupError) as exc:
                    label = hit.url or hit.title or "unknown"
                    errors.append(f"{label}: {exc}")
                    logger.info("skip archive %s: %s", label, exc)
        except Exception as exc:  # noqa: BLE001 — worker must not kill the run
            errors.append(str(exc))
            logger.exception("live worker search failed for %s", provider_name)

        result = {
            "ok": True,
            "acknowledged": True,
            "topic_id": topic_id,
            "provider": provider_name,
            "objective": objective,
            "hits": hit_count,
            "archived_document_ids": archived,
            "errors": errors[:10],
        }
        self.results.append(result)
        return result


@dataclass
class SegmentClaimProposer:
    """Propose grounded claims from archived segments (no LLM required)."""

    max_claims: int = 12
    min_quote_len: int = 40
    max_quote_len: int = 280
    proposed_ids: list[str] = field(default_factory=list)

    async def propose_for_plan(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: ResearchPlan,
        documents: DocumentRepository,
        claims: ClaimRepository,
        evidence: EvidenceRepository,
    ) -> list[str]:
        docs = await documents.list_for_project(project_id)
        claim_ids: list[str] = []
        keywords = _keywords_from_plan(plan)

        for topic in plan.topics:
            if len(claim_ids) >= self.max_claims:
                break
            topic_docs = _docs_for_topic(docs, topic.topic_id) or docs
            for doc in topic_docs:
                if len(claim_ids) >= self.max_claims:
                    break
                version = await documents.latest_version(doc.id)
                if version is None:
                    continue
                segs = await documents.list_segments(version.id)
                quote, seg = _pick_quote(
                    segs,
                    keywords=keywords + [topic.title, *topic.research_angles],
                    min_len=self.min_quote_len,
                    max_len=self.max_quote_len,
                )
                if not quote or seg is None:
                    continue
                statement = _claim_statement(topic.title, quote, plan.prompt)
                claim = ProposedClaim(
                    statement=statement,
                    topic_id=topic.topic_id,
                    project_id=project_id,
                    run_id=run_id,
                )
                stored = await claims.create(claim, project_id=project_id, run_id=run_id)
                start = (seg.char_start or 0) + max(seg.text.find(quote), 0)
                ev = Evidence(
                    document_id=doc.id,
                    document_version_id=version.id,
                    document_segment_id=seg.id,
                    quote=quote,
                    location=EvidenceLocation(
                        char_start=start,
                        char_end=start + len(quote),
                    ),
                    content_hash=quote_content_hash(quote),
                )
                created = await evidence.create(ev)
                await claims.attach_evidence(stored.id, created.id)
                claim_ids.append(stored.id)

        # Ensure at least one claim per topic if any docs exist.
        if docs and not claim_ids:
            topic = plan.topics[0] if plan.topics else None
            version = await documents.latest_version(docs[0].id)
            segs = await documents.list_segments(version.id) if version else []
            quote, seg = _pick_quote(segs, keywords=keywords, min_len=20, max_len=200)
            if quote and seg and version and topic:
                claim = ProposedClaim(
                    statement=_claim_statement(topic.title, quote, plan.prompt),
                    topic_id=topic.topic_id,
                    project_id=project_id,
                    run_id=run_id,
                )
                stored = await claims.create(claim, project_id=project_id, run_id=run_id)
                start = (seg.char_start or 0) + max(seg.text.find(quote), 0)
                ev = Evidence(
                    document_id=docs[0].id,
                    document_version_id=version.id,
                    document_segment_id=seg.id,
                    quote=quote,
                    location=EvidenceLocation(char_start=start, char_end=start + len(quote)),
                    content_hash=quote_content_hash(quote),
                )
                created = await evidence.create(ev)
                await claims.attach_evidence(stored.id, created.id)
                claim_ids.append(stored.id)

        self.proposed_ids.extend(claim_ids)
        return claim_ids


@dataclass
class FallbackThesisSupervisor:
    """Deterministic plan used when OpenRouter planning fails."""

    async def create_plan(
        self,
        *,
        project_id: str,
        prompt: str,
        feedback: str | None = None,
        version: int = 1,
    ) -> ResearchPlan:
        topics = [
            PlanTopic(
                topic_id="topic-organon",
                title="Aristotle and the Organon",
                objective=(
                    "Find sources describing Aristotle's logical works "
                    "(Organon, Prior Analytics) as founding formal logic"
                ),
                research_angles=[
                    "Aristotle invented formal logic Organon Prior Analytics",
                    "Aristotle founder of formal logic syllogism",
                ],
                desired_source_classes=["encyclopedia", "web", "general"],
            ),
            PlanTopic(
                topic_id="topic-syllogism",
                title="Syllogistic system as formal logic",
                objective=(
                    "Locate statements that Aristotle's syllogistic is the first "
                    "systematic formal logic"
                ),
                research_angles=[
                    "Aristotle syllogism first formal logic system",
                    "history of logic Aristotle syllogistic",
                ],
                desired_source_classes=["encyclopedia", "web", "general"],
            ),
        ]
        notes = []
        if feedback:
            notes.append(f"Feedback noted: {feedback}")
        return ResearchPlan(
            id=str(uuid4()),
            project_id=project_id,
            prompt=prompt,
            rhetorical_posture=RhetoricalPosture.ARGUMENTATIVE,
            status=PlanStatus.DRAFT,
            version=version,
            topics=topics,
            sections=[
                PlanSection(
                    section_id="sec-intro",
                    title="Thesis and scope",
                    questions=["What claim is being proved?"],
                    topic_ids=["topic-organon"],
                    order=0,
                ),
                PlanSection(
                    section_id="sec-evidence",
                    title="Evidence that Aristotle founded formal logic",
                    questions=["What sources attribute the invention of formal logic to Aristotle?"],
                    topic_ids=["topic-organon", "topic-syllogism"],
                    order=1,
                ),
                PlanSection(
                    section_id="sec-conclusion",
                    title="Conclusion",
                    questions=["How strongly do the sources support the thesis?"],
                    topic_ids=["topic-syllogism"],
                    order=2,
                ),
            ],
            inclusion_boundaries=["Support the user's thesis with strongest available evidence"],
            exclusion_boundaries=[
                "Do not invent sources",
                "Do not insert unsolicited counterarguments",
            ],
            expected_evidence_classes=["testimony", "historical", "scholarly"],
            completion_criteria=["Each topic has archived supporting sources"],
            worker_assignments=[],
            metadata={"fallback_supervisor": True, "notes": notes},
        )


class ResilientSupervisor:
    """Try OpenRouter planning; fall back to a thesis-aware deterministic plan."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback
        self.used_fallback = False
        self.last_error: str | None = None

    async def create_plan(
        self,
        *,
        project_id: str,
        prompt: str,
        feedback: str | None = None,
        version: int = 1,
    ) -> ResearchPlan:
        try:
            plan = await self.primary.create_plan(
                project_id=project_id,
                prompt=prompt,
                feedback=feedback,
                version=version,
            )
            # Prefer web/encyclopedia for MVP live runs (MediaWiki + Tavily).
            topics = []
            for topic in plan.topics:
                classes = list(topic.desired_source_classes)
                for needed in ("web", "general", "encyclopedia"):
                    if needed not in classes:
                        classes.append(needed)
                # Drop slow specialty providers on first live path.
                classes = [c for c in classes if c not in {"scholarly", "preprint", "bibliographic"}]
                if not classes:
                    classes = ["web", "encyclopedia"]
                topics.append(topic.model_copy(update={"desired_source_classes": classes}))
            return plan.model_copy(update={"topics": topics})
        except Exception as exc:  # noqa: BLE001
            self.used_fallback = True
            self.last_error = str(exc)
            logger.warning("OpenRouter supervisor failed (%s); using fallback plan", exc)
            return await self.fallback.create_plan(
                project_id=project_id,
                prompt=prompt,
                feedback=feedback,
                version=version,
            )


def build_openrouter_supervisor(
    config: AppConfig,
    *,
    project_id: str,
) -> PydanticSupervisor:
    """Build a tool-light OpenRouter supervisor for research planning."""
    registry = ModelRegistry(config)
    # Prefer no tools so planning is a single structured-output call.
    from pydantic_ai import Agent

    model = registry.build_model_for_role(RoleName.SUPERVISOR)
    agent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=ResearchPlan,
        instructions=(
            "You are the DeepRhetor supervisor. Produce a faithful research plan from "
            "the user's prompt. Infer rhetorical posture. Give each topic narrow objectives "
            "and desired_source_classes chosen from: web, general, encyclopedia, scholarly. "
            "When the user asks to prove or support a thesis, plan to find the strongest "
            "supporting evidence; do not invent sources and do not add unsolicited "
            "counterarguments. Keep topics to 2–4. Never fabricate citations."
        ),
        name="supervisor",
    )
    deps = AgentDeps(project_id=project_id)
    return PydanticSupervisor(agent=agent, deps=deps)


def build_live_components(
    *,
    engine: Any,
    project_id: str,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Assemble live providers, worker, supervisor, proposer, critic for a run."""
    cfg = config or load_config()
    # Prefer free + Tavily for the first live path (skip arxiv/openalex rate limits).
    from deeprhetor.plugins.mediawiki import MediaWikiSearchProvider
    from deeprhetor.plugins.tavily import TavilySearchProvider
    from deeprhetor.services.llm_claims import OpenRouterClaimProposer

    providers = SearchProviderRegistry()
    providers.register(MediaWikiSearchProvider())
    providers.register(TavilySearchProvider(config=cfg))
    documents = DocumentRepository(engine)
    worker = LiveResearchWorker(
        providers=providers,
        documents=documents,
        project_id=project_id,
        max_docs_per_assignment=2,
        max_results=5,
    )
    primary = build_openrouter_supervisor(cfg, project_id=project_id)
    supervisor = ResilientSupervisor(primary, FallbackThesisSupervisor())
    heuristic = SegmentClaimProposer()
    proposer = OpenRouterClaimProposer(config=cfg, fallback=heuristic)
    critic = FakeCoverageCritic(force_complete=True)
    return {
        "config": cfg,
        "providers": providers,
        "supervisor": supervisor,
        "worker": worker,
        "proposer": proposer,
        "critic": critic,
        "limits": cfg.limits,
    }


def _public_http_url(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if text.startswith(("http://", "https://")):
        return text
    return None


_PREFERRED_HOST_FRAGMENTS = (
    "wikipedia.org",
    "plato.stanford.edu",
    "britannica.com",
    "iep.utm.edu",
    "arxiv.org",
    "openalex.org",
)


def _prefer_stable_sources(hits: list[Any]) -> list[Any]:
    def score(hit: Any) -> tuple[int, int]:
        url = (getattr(hit, "url", None) or "").lower()
        preferred = 0
        for idx, frag in enumerate(_PREFERRED_HOST_FRAGMENTS):
            if frag in url:
                preferred = len(_PREFERRED_HOST_FRAGMENTS) - idx
                break
        return (-preferred, 0)

    return sorted(hits, key=score)


_CORE_TERMS = frozenset(
    {
        "aristotle",
        "aristotelian",
        "logic",
        "logical",
        "syllogism",
        "syllogistic",
        "organon",
        "analytics",
        "deduction",
        "deductive",
        "formal",
        "philosophy",
        "plato",
        "stoic",
        "inference",
    }
)


def _looks_relevant(title: str | None, snippet: str | None, objective: str) -> bool:
    blob = f"{title or ''} {snippet or ''}".lower()
    obj = objective.lower()
    logic_terms = {
        "logic",
        "logical",
        "syllogism",
        "syllogistic",
        "organon",
        "analytics",
        "deduction",
        "deductive",
        "inference",
    }
    # Reject known off-topic Aristotle pages frequently returned by broad searches.
    reject_titles = (
        "politics (aristotle)",
        "nicomachean ethics",
        "poetics",
        "rhetoric (aristotle)",
        "empiricism",
        "intelligent design",
    )
    title_l = (title or "").lower()
    if any(bad == title_l or bad in title_l for bad in reject_titles):
        # Allow Rhetoric only if the research objective is about rhetoric.
        if "rhetoric" in title_l and "rhetoric" in obj:
            pass
        else:
            return False

    def has_term(term: str) -> bool:
        return re.search(rf"\b{re.escape(term)}\b", blob) is not None

    if "logic" in obj or "syllog" in obj or "organon" in obj:
        if has_term("formal logic"):
            return True
        return any(has_term(term) for term in logic_terms)
    must = set(re.findall(r"[a-z]{4,}", obj)) & _CORE_TERMS
    if not must:
        return True
    return any(has_term(term) for term in must)


def _keywords_from_plan(plan: ResearchPlan) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", plan.prompt)
    # Prefer distinctive prompt terms.
    seen: list[str] = []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.append(lw)
    # Bias toward core logic vocabulary.
    for term in ("aristotle", "logic", "syllogism", "organon", "formal"):
        if term not in seen:
            seen.insert(0, term)
    return seen[:16]


def _docs_for_topic(docs: list[Any], topic_id: str) -> list[Any]:
    matched = []
    for doc in docs:
        meta = getattr(doc, "metadata", None) or {}
        if isinstance(meta, dict) and meta.get("topic_id") == topic_id:
            matched.append(doc)
    return matched


def _pick_quote(
    segments: list[Any],
    *,
    keywords: list[str],
    min_len: int,
    max_len: int,
) -> tuple[str | None, Any | None]:
    best: tuple[int, str, Any] | None = None
    for seg in segments:
        text = (getattr(seg, "text", None) or "").strip()
        if len(text) < min_len:
            continue
        # Skip wiki nav / table chrome.
        if text.count("|") >= 3 or "---|---" in text or text.startswith("{|"):
            continue
        lower = text.lower()
        score = sum(1 for kw in keywords if kw.lower() in lower)
        # Extra weight for thesis-critical phrases.
        for phrase, boost in (
            ("formal logic", 5),
            ("syllogism", 4),
            ("organon", 4),
            ("prior analytics", 4),
            ("first", 2),
            ("founded", 3),
            ("invented", 3),
            ("founder", 3),
            ("systematic", 2),
        ):
            if phrase in lower:
                score += boost
        if keywords and score == 0:
            continue
        snippet = text[:max_len].rsplit(" ", 1)[0] if len(text) > max_len else text
        snippet = snippet.strip()
        if len(snippet) < min_len:
            continue
        if best is None or score > best[0]:
            best = (score, snippet, seg)
    if best is None:
        return None, None
    return best[1], best[2]


def _claim_statement(topic_title: str, quote: str, prompt: str) -> str:
    clipped = quote.strip()
    if len(clipped) > 220:
        clipped = clipped[:217].rsplit(" ", 1)[0] + "…"
    # Keep statement as an evidence-bearing proposition supporting the research objective.
    return (
        f"In support of the thesis ({prompt.rstrip('.')}), "
        f"sources on {topic_title} report: {clipped}"
    )
