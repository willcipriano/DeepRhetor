"""Initial DeepRhetor project schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("PRAGMA foreign_keys=ON")

    # --- schema tracking ---
    op.execute(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # --- Project ---
    op.execute(
        """
        CREATE TABLE project (
            id TEXT PRIMARY KEY NOT NULL,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE configuration_snapshot (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            label TEXT,
            settings_json TEXT NOT NULL,
            model_presets_json TEXT NOT NULL DEFAULT '{}',
            credential_refs_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )

    # --- Workflow ---
    op.execute(
        """
        CREATE TABLE run (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            configuration_snapshot_id TEXT REFERENCES configuration_snapshot(id),
            status TEXT NOT NULL DEFAULT 'created',
            plan_version INTEGER,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE task (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
            parent_task_id TEXT REFERENCES task(id),
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assignment_json TEXT,
            idempotency_key TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT,
            UNIQUE(run_id, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE task_dependency (
            task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
            depends_on_task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
            PRIMARY KEY (task_id, depends_on_task_id),
            CHECK (task_id != depends_on_task_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE checkpoint (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
            node_name TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE event (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT REFERENCES run(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            level TEXT NOT NULL DEFAULT 'info',
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE error (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT REFERENCES run(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            code TEXT,
            message TEXT NOT NULL,
            traceback TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # --- Planning ---
    op.execute(
        """
        CREATE TABLE research_plan (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft',
            rhetorical_posture TEXT,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            approved_at TEXT,
            UNIQUE(project_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE plan_topic (
            id TEXT PRIMARY KEY NOT NULL,
            plan_id TEXT NOT NULL REFERENCES research_plan(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            topic_json TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE plan_section (
            id TEXT PRIMARY KEY NOT NULL,
            plan_id TEXT NOT NULL REFERENCES research_plan(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            section_json TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE plan_amendment (
            id TEXT PRIMARY KEY NOT NULL,
            plan_id TEXT NOT NULL REFERENCES research_plan(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            reason TEXT NOT NULL,
            amendment_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # --- Discovery ---
    op.execute(
        """
        CREATE TABLE search_query (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            provider TEXT NOT NULL,
            query TEXT NOT NULL,
            request_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            idempotency_key TEXT,
            UNIQUE(idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE search_hit (
            id TEXT PRIMARY KEY NOT NULL,
            search_query_id TEXT NOT NULL REFERENCES search_query(id) ON DELETE CASCADE,
            title TEXT,
            url TEXT,
            snippet TEXT,
            score REAL,
            hit_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE provider_call (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            provider TEXT NOT NULL,
            operation TEXT NOT NULL,
            idempotency_key TEXT,
            request_json TEXT,
            response_json TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            latency_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(idempotency_key)
        )
        """
    )

    # --- Corpus ---
    op.execute(
        """
        CREATE TABLE document (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            canonical_url TEXT,
            title TEXT,
            media_type TEXT,
            source_class TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_version (
            id TEXT PRIMARY KEY NOT NULL,
            document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
            version INTEGER NOT NULL DEFAULT 1,
            original_url TEXT,
            content_sha256 TEXT NOT NULL,
            normalized_sha256 TEXT,
            parser TEXT,
            parser_version TEXT,
            retrieved_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(document_id, version),
            UNIQUE(content_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_blob (
            id TEXT PRIMARY KEY NOT NULL,
            document_version_id TEXT NOT NULL REFERENCES document_version(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            media_type TEXT,
            compression TEXT,
            byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            data BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_segment (
            id TEXT PRIMARY KEY NOT NULL,
            document_version_id TEXT NOT NULL REFERENCES document_version(id) ON DELETE CASCADE,
            segment_index INTEGER NOT NULL,
            page INTEGER,
            section_path TEXT,
            char_start INTEGER,
            char_end INTEGER,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            UNIQUE(document_version_id, segment_index)
        )
        """
    )
    op.execute(
        """
        CREATE VIRTUAL TABLE document_fts USING fts5(
            segment_id UNINDEXED,
            document_version_id UNINDEXED,
            text
        )
        """
    )

    # --- Assessment ---
    op.execute(
        """
        CREATE TABLE relevance_assessment (
            id TEXT PRIMARY KEY NOT NULL,
            document_id TEXT REFERENCES document(id) ON DELETE CASCADE,
            search_hit_id TEXT REFERENCES search_hit(id) ON DELETE SET NULL,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            is_relevant INTEGER NOT NULL,
            score REAL,
            rationale TEXT,
            assessment_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE segment_scan (
            id TEXT PRIMARY KEY NOT NULL,
            document_segment_id TEXT NOT NULL REFERENCES document_segment(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            summary TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            batch_index INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_scan (
            id TEXT PRIMARY KEY NOT NULL,
            document_version_id TEXT NOT NULL REFERENCES document_version(id) ON DELETE CASCADE,
            total_segments INTEGER NOT NULL DEFAULT 0,
            completed_segments INTEGER NOT NULL DEFAULT 0,
            failed_segments INTEGER NOT NULL DEFAULT 0,
            is_complete INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # --- Knowledge ---
    op.execute(
        """
        CREATE TABLE claim (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            topic_id TEXT,
            statement TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            claim_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE evidence (
            id TEXT PRIMARY KEY NOT NULL,
            document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
            document_version_id TEXT NOT NULL REFERENCES document_version(id) ON DELETE CASCADE,
            document_segment_id TEXT REFERENCES document_segment(id) ON DELETE SET NULL,
            quote TEXT NOT NULL,
            location_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE claim_evidence (
            claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'supports',
            directness TEXT NOT NULL DEFAULT 'direct',
            explanation TEXT,
            PRIMARY KEY (claim_id, evidence_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE claim_relation (
            id TEXT PRIMARY KEY NOT NULL,
            from_claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            to_claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            relation TEXT NOT NULL,
            notes TEXT,
            CHECK (from_claim_id != to_claim_id)
        )
        """
    )
    op.execute(
        """
        CREATE VIRTUAL TABLE claim_fts USING fts5(
            claim_id UNINDEXED,
            statement
        )
        """
    )

    # --- Writing ---
    op.execute(
        """
        CREATE TABLE outline (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            plan_id TEXT REFERENCES research_plan(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            outline_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE draft (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            outline_id TEXT REFERENCES outline(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            draft_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE draft_section (
            id TEXT PRIMARY KEY NOT NULL,
            draft_id TEXT NOT NULL REFERENCES draft(id) ON DELETE CASCADE,
            section_id TEXT NOT NULL,
            title TEXT NOT NULL,
            prose TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            section_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(draft_id, section_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE citation_key (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            claim_id TEXT REFERENCES claim(id) ON DELETE SET NULL,
            evidence_id TEXT REFERENCES evidence(id) ON DELETE SET NULL,
            document_id TEXT REFERENCES document(id) ON DELETE SET NULL,
            bib_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(project_id, key)
        )
        """
    )

    # --- Operations ---
    op.execute(
        """
        CREATE TABLE model_call (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
            role TEXT,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            idempotency_key TEXT,
            request_json TEXT,
            response_json TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            latency_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE usage_record (
            id TEXT PRIMARY KEY NOT NULL,
            model_call_id TEXT REFERENCES model_call(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_tokens INTEGER,
            reasoning_tokens INTEGER,
            estimated_cost_usd REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE artifact (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
            kind TEXT NOT NULL,
            media_type TEXT,
            path_or_name TEXT,
            sha256 TEXT,
            byte_size INTEGER,
            data BLOB,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE validation_result (
            id TEXT PRIMARY KEY NOT NULL,
            draft_id TEXT REFERENCES draft(id) ON DELETE CASCADE,
            outcome TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # Seed schema version row
    op.execute("INSERT INTO schema_version (version) VALUES (1)")


def downgrade() -> None:
    tables = [
        "validation_result",
        "artifact",
        "usage_record",
        "model_call",
        "citation_key",
        "draft_section",
        "draft",
        "outline",
        "claim_fts",
        "claim_relation",
        "claim_evidence",
        "evidence",
        "claim",
        "document_scan",
        "segment_scan",
        "relevance_assessment",
        "document_fts",
        "document_segment",
        "document_blob",
        "document_version",
        "document",
        "provider_call",
        "search_hit",
        "search_query",
        "plan_amendment",
        "plan_section",
        "plan_topic",
        "research_plan",
        "error",
        "event",
        "checkpoint",
        "task_dependency",
        "task",
        "run",
        "configuration_snapshot",
        "project",
        "schema_version",
    ]
    for name in tables:
        op.execute(f"DROP TABLE IF EXISTS {name}")
