"""Role names, preset tiers, and tool-name inventories."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RoleName(StrEnum):
    SUPERVISOR = "supervisor"
    TOPIC_WORKER = "topic_worker"
    VERIFIER = "verifier"
    COVERAGE_CRITIC = "coverage_critic"
    OUTLINE_EDITOR = "outline_editor"
    WRITER = "writer"


# Maps each role to a cheap/mid/frontier preset key.
ROLE_PRESET: Final[dict[str, str]] = {
    RoleName.SUPERVISOR: "mid",
    RoleName.TOPIC_WORKER: "cheap",
    RoleName.VERIFIER: "mid",
    RoleName.COVERAGE_CRITIC: "mid",
    RoleName.OUTLINE_EDITOR: "mid",
    RoleName.WRITER: "frontier",
}

SUPERVISOR_TOOLS: Final[tuple[str, ...]] = (
    "list_provider_capabilities",
    "read_project_brief",
    "create_research_plan",
    "create_worker_assignment",
    "set_task_dependencies",
    "inspect_topic_progress",
    "inspect_coverage_summary",
    "request_gap_research",
    "finalize_research",
)

TOPIC_WORKER_TOOLS: Final[tuple[str, ...]] = (
    "read_assignment",
    "search_source",
    "list_search_candidates",
    "fetch_document",
    "record_relevance",
    "search_archived_documents",
    "read_document_segments",
    "record_segment_scan",
    "complete_document_scan",
    "propose_claim",
    "attach_evidence",
    "record_source_note",
)

VERIFIER_TOOLS: Final[tuple[str, ...]] = (
    "list_proposed_claims",
    "read_claim_evidence",
    "read_exact_source_span",
    "approve_claim",
    "reject_claim",
    "request_claim_correction",
    "record_claim_relationship",
    "find_duplicate_claims",
)

CRITIC_TOOLS: Final[tuple[str, ...]] = (
    "read_approved_plan",
    "inspect_claim_coverage",
    "find_unsupported_claims",
    "find_unscanned_documents",
    "inspect_source_diversity",
    "record_coverage_report",
    "request_research_gap",
    "mark_research_complete",
)

# Outline editor consolidates plan + claims into an outline (no web/fetch/approve).
OUTLINE_EDITOR_TOOLS: Final[tuple[str, ...]] = (
    "read_approved_plan",
    "search_approved_claims",
    "read_existing_outline",
    "save_outline",
)

WRITER_TOOLS: Final[tuple[str, ...]] = (
    "read_authoritative_prompt",
    "read_approved_outline",
    "search_approved_claims",
    "get_section_claim_packet",
    "resolve_citation_key",
    "read_existing_draft_sections",
    "save_draft_section",
)

ROLE_TOOLS: Final[dict[str, tuple[str, ...]]] = {
    RoleName.SUPERVISOR: SUPERVISOR_TOOLS,
    RoleName.TOPIC_WORKER: TOPIC_WORKER_TOOLS,
    RoleName.VERIFIER: VERIFIER_TOOLS,
    RoleName.COVERAGE_CRITIC: CRITIC_TOOLS,
    RoleName.OUTLINE_EDITOR: OUTLINE_EDITOR_TOOLS,
    RoleName.WRITER: WRITER_TOOLS,
}

# Substrings / names the writer must never receive.
WRITER_FORBIDDEN_TOOL_PATTERNS: Final[tuple[str, ...]] = (
    "search_source",
    "fetch_document",
    "list_search_candidates",
    "approve_claim",
    "reject_claim",
    "request_claim_correction",
    "execute_sql",
)


def tool_names_for_role(role: str) -> frozenset[str]:
    """Return the closed tool surface for a role."""
    tools = ROLE_TOOLS.get(role)
    if tools is None:
        raise KeyError(f"Unknown role: {role}")
    return frozenset(tools)
