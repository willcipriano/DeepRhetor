"""Capability-aware worker dispatch (not topic × provider Cartesian product)."""

from __future__ import annotations

from deeprhetor.domain.planning import PlanTopic, ResearchPlan, WorkerAssignment
from deeprhetor.domain.sources import ProviderDescriptor


def matching_providers_for_topic(
    topic: PlanTopic,
    descriptors: list[ProviderDescriptor],
) -> list[ProviderDescriptor]:
    """Return providers whose source_classes intersect the topic's desired classes.

    Topics with no desired_source_classes do not match any provider here; callers
    may still honor explicit supervisor worker_assignments.
    """
    desired = {c.lower() for c in topic.desired_source_classes}
    if not desired:
        return []
    matches: list[ProviderDescriptor] = []
    for desc in descriptors:
        classes = {c.lower() for c in desc.source_classes}
        if desired & classes:
            matches.append(desc)
    return matches


def build_capability_aware_assignments(
    plan: ResearchPlan,
    descriptors: list[ProviderDescriptor],
) -> list[WorkerAssignment]:
    """Build worker assignments from the plan without a full Cartesian fan-out.

    Precedence:
    1. Explicit ``plan.worker_assignments`` whose provider is registered (or
       named as a source class that matches a registered provider).
    2. Otherwise one assignment per (topic, matching provider) based on
       ``desired_source_classes`` ∩ ``ProviderDescriptor.source_classes``.
    """
    by_name = {d.name: d for d in descriptors}
    class_index: dict[str, list[ProviderDescriptor]] = {}
    for desc in descriptors:
        for cls in desc.source_classes:
            class_index.setdefault(cls.lower(), []).append(desc)

    if plan.worker_assignments:
        resolved: list[WorkerAssignment] = []
        for assignment in plan.worker_assignments:
            key = assignment.provider_or_class
            if key in by_name:
                resolved.append(assignment)
                continue
            # Treat provider_or_class as a source class label.
            for desc in class_index.get(key.lower(), []):
                resolved.append(
                    assignment.model_copy(update={"provider_or_class": desc.name})
                )
        if resolved:
            return resolved

    topics = {t.topic_id: t for t in plan.topics}
    built: list[WorkerAssignment] = []
    seen: set[tuple[str, str]] = set()
    for topic in plan.topics:
        for desc in matching_providers_for_topic(topic, descriptors):
            pair = (topic.topic_id, desc.name)
            if pair in seen:
                continue
            seen.add(pair)
            angle = topic.research_angles[0] if topic.research_angles else topic.objective
            built.append(
                WorkerAssignment(
                    topic_id=topic.topic_id,
                    objective=angle,
                    provider_or_class=desc.name,
                    acceptance_criteria=[
                        f"Cover topic '{topic.title}' via {desc.name}",
                    ],
                    exclusions=list(topic.exclusions),
                )
            )
    # Ignore unused local for lint clarity in case of empty plans.
    _ = topics
    return built


def assignment_idempotency_key(
    *,
    run_id: str,
    plan_version: int,
    topic_id: str,
    provider: str,
) -> str:
    return f"run:{run_id}:plan:{plan_version}:topic:{topic_id}:provider:{provider}"
