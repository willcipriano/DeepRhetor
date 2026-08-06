"""Domain model smoke tests."""

from __future__ import annotations

from deeprhetor.domain import (
    ClaimStatus,
    ProposedClaim,
    ResearchPlan,
    SearchRequest,
    SCHEMA_VERSION,
)


def test_versioned_models_round_trip() -> None:
    plan = ResearchPlan(project_id="p1", prompt="Test prompt")
    assert plan.schema_version == SCHEMA_VERSION
    assert plan.model_dump()["prompt"] == "Test prompt"

    claim = ProposedClaim(statement="Water boils at 100C at 1 atm")
    assert claim.status == ClaimStatus.PROPOSED

    req = SearchRequest(query="boiling point", provider="tavily")
    assert req.max_results == 10
