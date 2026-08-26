"""SQLite persistence for incidents, evidence inputs, and audit events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from .investigation import legacy_investigation
from .models import NormalizedInput, utc_now


JSON_COLUMNS = {
    "devices_json": "devices",
    "evidence_json": "evidence",
    "analysis_json": "analysis",
    "investigation_json": "investigation",
    "onsite_card_json": "onsite_card",
    "cc_reminder_json": "cc_reminder",
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class IncidentStore:
    """Small repository that opens a fresh connection per operation."""

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    site TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    correlation_key TEXT NOT NULL,
                    identity_keys TEXT NOT NULL,
                    devices_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    onsite_card_json TEXT NOT NULL,
                    cc_reminder_json TEXT NOT NULL,
                    communication_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_status_updated
                    ON incidents(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_incidents_correlation
                    ON incidents(correlation_key, status);

                CREATE TABLE IF NOT EXISTS event_inputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );

                CREATE TABLE IF NOT EXISTS facility_profiles (
                    site TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    criticality TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_reference TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS facility_profile_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site TEXT NOT NULL,
                    previous_json TEXT NOT NULL,
                    current_json TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS record_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_annotations_record
                    ON record_annotations(record_type, record_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    source_key TEXT PRIMARY KEY,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_cards (
                    card_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    published_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_cards_domain
                    ON knowledge_cards(domain, lifecycle_status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS knowledge_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    release_status TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY(card_id) REFERENCES knowledge_cards(card_id),
                    UNIQUE(card_id, version)
                );

                CREATE TABLE IF NOT EXISTS prompt_definitions (
                    prompt_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    published_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    release_status TEXT NOT NULL,
                    system_content TEXT NOT NULL,
                    user_template TEXT NOT NULL,
                    variables_json TEXT NOT NULL,
                    output_schema_json TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY(prompt_key) REFERENCES prompt_definitions(prompt_key),
                    UNIQUE(prompt_key, version)
                );

                CREATE TABLE IF NOT EXISTS constraint_profiles (
                    policy_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    published_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS constraint_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    release_status TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY(policy_key) REFERENCES constraint_profiles(policy_key),
                    UNIQUE(policy_key, version)
                );

                CREATE TABLE IF NOT EXISTS retrieval_test_runs (
                    id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    query_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    knowledge_version TEXT NOT NULL,
                    constraint_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_retrieval_test_runs_created
                    ON retrieval_test_runs(created_at DESC);

                CREATE TABLE IF NOT EXISTS release_runs (
                    id TEXT PRIMARY KEY,
                    asset_type TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    status TEXT NOT NULL,
                    test_summary_json TEXT NOT NULL,
                    diff_json TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    id TEXT PRIMARY KEY,
                    release_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES evaluation_runs(id)
                );

                CREATE TABLE IF NOT EXISTS model_providers (
                    provider_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    secret_configured INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_policies (
                    policy_key TEXT PRIMARY KEY,
                    provider_key TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    data_policy_json TEXT NOT NULL,
                    fallback_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(provider_key) REFERENCES model_providers(provider_key)
                );

                CREATE TABLE IF NOT EXISTS rag_runs (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    knowledge_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model_provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rag_runs_incident
                    ON rag_runs(incident_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS rag_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES rag_runs(id),
                    UNIQUE(run_id, step_order)
                );

                CREATE TABLE IF NOT EXISTS rag_hits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    card_version TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    retrieval_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES rag_runs(id)
                );

                CREATE TABLE IF NOT EXISTS asset_devices (
                    sn TEXT PRIMARY KEY,
                    site TEXT NOT NULL,
                    rack_position TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_order_snapshots (
                    id TEXT PRIMARY KEY,
                    order_no TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    site TEXT NOT NULL,
                    target_sn TEXT NOT NULL,
                    rack_position TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    from_reinstall TEXT NOT NULL,
                    power_policy TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    imported_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_work_order_snapshots_order
                    ON work_order_snapshots(order_no, created_at DESC);

                CREATE TABLE IF NOT EXISTS operation_cases (
                    id TEXT PRIMARY KEY,
                    work_order_snapshot_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    identity_status TEXT NOT NULL,
                    permission_status TEXT NOT NULL,
                    observed_sn TEXT NOT NULL,
                    scan_method TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    review_mode TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    result_reason TEXT NOT NULL,
                    result_details TEXT NOT NULL,
                    online_sn TEXT NOT NULL,
                    offline_sn TEXT NOT NULL,
                    timeout_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(work_order_snapshot_id) REFERENCES work_order_snapshots(id)
                );
                CREATE INDEX IF NOT EXISTS idx_operation_cases_status
                    ON operation_cases(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS operation_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    decided_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operation_cases(id)
                );

                CREATE TABLE IF NOT EXISTS operation_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    review_mode TEXT NOT NULL,
                    expected_sn TEXT NOT NULL,
                    observed_sn TEXT NOT NULL,
                    rack_position TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operation_cases(id)
                );

                CREATE TABLE IF NOT EXISTS operation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operation_cases(id)
                );

                CREATE TABLE IF NOT EXISTS integration_platforms (
                    platform_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    platform_type TEXT NOT NULL,
                    connection_state TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS integration_events (
                    id TEXT PRIMARY KEY,
                    platform_key TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    site TEXT NOT NULL,
                    explicit_incident_key TEXT NOT NULL,
                    derived_incident_key TEXT NOT NULL,
                    entity_json TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    normalized_json TEXT NOT NULL,
                    field_provenance_json TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    correlation_json TEXT NOT NULL,
                    simulation INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(platform_key) REFERENCES integration_platforms(platform_key),
                    UNIQUE(platform_key, source_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_integration_events_time
                    ON integration_events(site, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_integration_events_incident
                    ON integration_events(incident_id, occurred_at);

                CREATE TABLE IF NOT EXISTS topology_entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    canonical_key TEXT NOT NULL UNIQUE,
                    attributes_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS topology_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_entity_id TEXT NOT NULL,
                    to_entity_id TEXT NOT NULL,
                    link_type TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(from_entity_id) REFERENCES topology_entities(entity_id),
                    FOREIGN KEY(to_entity_id) REFERENCES topology_entities(entity_id),
                    UNIQUE(from_entity_id, to_entity_id, link_type)
                );
                CREATE INDEX IF NOT EXISTS idx_topology_links_from
                    ON topology_links(from_entity_id, link_type);
                CREATE INDEX IF NOT EXISTS idx_topology_links_to
                    ON topology_links(to_entity_id, link_type);

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    max_rounds INTEGER NOT NULL,
                    stop_reason TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_incident
                    ON agent_runs(incident_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS agent_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    round_no INTEGER NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_args_json TEXT NOT NULL,
                    tool_output_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    hypotheses_before_json TEXT NOT NULL,
                    hypotheses_after_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    model_output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id),
                    UNIQUE(run_id, round_no)
                );

                CREATE TABLE IF NOT EXISTS raw_access_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_raw_access_record
                    ON raw_access_audit(record_type, record_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS backup_runs (
                    id TEXT PRIMARY KEY,
                    backup_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS managed_alerts (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    site TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    entity_json TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    recovered_at TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    suppression_reason TEXT NOT NULL,
                    parent_alert_id TEXT NOT NULL,
                    requires_service_validation INTEGER NOT NULL,
                    data_quality_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_alerts_active_fingerprint
                    ON managed_alerts(fingerprint)
                    WHERE lifecycle_status IN ('firing', 'acknowledged', 'suppressed', 'silenced');
                CREATE INDEX IF NOT EXISTS idx_managed_alerts_status_time
                    ON managed_alerts(lifecycle_status, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_managed_alerts_incident
                    ON managed_alerts(incident_id, last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS maintenance_windows (
                    id TEXT PRIMARY KEY,
                    site TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maintenance_active
                    ON maintenance_windows(site, starts_at, ends_at);

                CREATE TABLE IF NOT EXISTS source_health (
                    source_system TEXT PRIMARY KEY,
                    connection_status TEXT NOT NULL,
                    last_received_at TEXT NOT NULL,
                    received_count INTEGER NOT NULL,
                    rejected_count INTEGER NOT NULL,
                    queue_depth INTEGER NOT NULL,
                    dropped_count INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    expected_entities INTEGER NOT NULL,
                    reporting_entities INTEGER NOT NULL,
                    coverage_percent REAL NOT NULL,
                    details_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS identity_assertions (
                    id TEXT PRIMARY KEY,
                    entity_key TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_value TEXT NOT NULL,
                    authority_rank INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_identity_assertions_entity
                    ON identity_assertions(entity_key, field_name, authority_rank, observed_at DESC);

                CREATE TABLE IF NOT EXISTS identity_conflicts (
                    id TEXT PRIMARY KEY,
                    entity_key TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    authoritative_source TEXT NOT NULL,
                    authoritative_value TEXT NOT NULL,
                    conflicting_source TEXT NOT NULL,
                    conflicting_value TEXT NOT NULL,
                    status TEXT NOT NULL,
                    operation_blocked INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    resolved_by TEXT NOT NULL,
                    resolution TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_identity_conflicts_status
                    ON identity_conflicts(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS change_events (
                    id TEXT PRIMARY KEY,
                    site TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    changed_by TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    causality TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_change_events_entity_time
                    ON change_events(site, entity_key, changed_at DESC);

                CREATE TABLE IF NOT EXISTS duty_rosters (
                    id TEXT PRIMARY KEY,
                    site TEXT NOT NULL,
                    team TEXT NOT NULL,
                    person TEXT NOT NULL,
                    shift_start TEXT NOT NULL,
                    shift_end TEXT NOT NULL,
                    escalation_person TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_duty_rosters_shift
                    ON duty_rosters(site, team, shift_start, shift_end);

                CREATE TABLE IF NOT EXISTS incident_assignments (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    assignee TEXT NOT NULL,
                    team TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    deferred_reason TEXT NOT NULL,
                    escalated_to TEXT NOT NULL,
                    assigned_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incident_assignments_active
                    ON incident_assignments(status, due_at);

                CREATE TABLE IF NOT EXISTS correlation_feedback (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    alert_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    target_incident_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_correlation_feedback_incident
                    ON correlation_feedback(incident_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS investigation_metrics (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    dimensions_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_investigation_metrics_name
                    ON investigation_metrics(metric_name, recorded_at DESC);

                CREATE TABLE IF NOT EXISTS public_dataset_imports (
                    id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    alert_count INTEGER NOT NULL,
                    error_count INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_public_dataset_imports_dataset
                    ON public_dataset_imports(dataset_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS drill_runs (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    category TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    playback_mode TEXT NOT NULL,
                    analysis_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step_id TEXT NOT NULL,
                    logical_time INTEGER NOT NULL,
                    incident_ids_json TEXT NOT NULL,
                    location_json TEXT NOT NULL,
                    impact_path_json TEXT NOT NULL,
                    final_diagnosis TEXT NOT NULL,
                    final_status TEXT NOT NULL,
                    score_json TEXT NOT NULL,
                    started_by TEXT NOT NULL,
                    started_role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_drill_runs_created
                    ON drill_runs(created_at DESC);

                CREATE TABLE IF NOT EXISTS drill_run_secrets (
                    run_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    truth_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES drill_runs(id)
                );

                CREATE TABLE IF NOT EXISTS drill_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES drill_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_drill_steps_run
                    ON drill_steps(run_id, id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "investigation_json" not in columns:
                connection.execute(
                    "ALTER TABLE incidents ADD COLUMN investigation_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (1, 'data_ai_assets_foundation', ?)
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (4, 'minimum_production_loop', ?)
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (5, 'interactive_fault_drills', ?)
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (2, 'onsite_work_order_operations', ?)
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
                VALUES (3, 'ai_multiplatform_lab_foundation', ?)
                """,
                (utc_now(),),
            )

    def list_facility_profiles(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM facility_profiles ORDER BY site"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_facility_profile(self, site: str) -> Optional[Dict[str, Any]]:
        normalized = str(site or "").strip().upper()
        if not normalized:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM facility_profiles WHERE site = ?", (normalized,)
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_facility_profile(self, profile: Mapping[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        site = str(profile["site"]).strip().upper()
        current = {
            "site": site,
            "display_name": str(profile.get("display_name") or site),
            "criticality": str(profile.get("criticality") or "unknown"),
            "source": str(profile.get("source") or "local_config"),
            "source_reference": str(profile.get("source_reference") or ""),
            "effective_at": str(profile.get("effective_at") or now),
            "updated_at": now,
        }
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM facility_profiles WHERE site = ?", (site,)
            ).fetchone()
            previous = dict(row) if row is not None else {}
            connection.execute(
                """
                INSERT INTO facility_profiles (
                    site, display_name, criticality, source, source_reference,
                    effective_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site) DO UPDATE SET
                    display_name=excluded.display_name,
                    criticality=excluded.criticality,
                    source=excluded.source,
                    source_reference=excluded.source_reference,
                    effective_at=excluded.effective_at,
                    updated_at=excluded.updated_at
                """,
                (
                    current["site"],
                    current["display_name"],
                    current["criticality"],
                    current["source"],
                    current["source_reference"],
                    current["effective_at"],
                    current["updated_at"],
                ),
            )
            if previous != current:
                connection.execute(
                    """
                    INSERT INTO facility_profile_history (
                        site, previous_json, current_json, changed_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (site, _dump(previous), _dump(current), now),
                )
        result = self.get_facility_profile(site)
        assert result is not None
        return result

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for raw_name, public_name in JSON_COLUMNS.items():
            fallback: Any = [] if public_name in {"devices", "evidence"} else {}
            result[public_name] = _load(result.pop(raw_name, ""), fallback)
        result["affected_count"] = len(result["devices"])
        if not result["investigation"]:
            result["investigation"] = legacy_investigation(result)
        return result

    def list_incidents(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incidents
                ORDER BY CASE status
                    WHEN 'new' THEN 0
                    WHEN 'processing' THEN 1
                    ELSE 2
                END, updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if row is None:
                return None
            inputs = connection.execute(
                """
                SELECT id, source, event_time, payload_json, created_at
                FROM event_inputs WHERE incident_id = ? ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
            audits = connection.execute(
                """
                SELECT action, details_json, created_at
                FROM audit_log WHERE incident_id = ? ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
        result = self._decode(row)
        result["inputs"] = [
            {
                "id": item["id"],
                "source": item["source"],
                "event_time": item["event_time"],
                "payload": _load(item["payload_json"], {}),
                "created_at": item["created_at"],
            }
            for item in inputs
        ]
        result["audit_log"] = [
            {
                "action": item["action"],
                "details": _load(item["details_json"], {}),
                "created_at": item["created_at"],
            }
            for item in audits
        ]
        return result

    def find_merge_candidate(
        self, event: NormalizedInput, category: str, correlation_key: str
    ) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            if event.labels.get("incident_key"):
                row = connection.execute(
                    """
                    SELECT * FROM incidents
                    WHERE correlation_key = ? AND status != 'resolved'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (correlation_key,),
                ).fetchone()
            elif event.device.identity_key():
                identity = f"|{event.device.identity_key()}|"
                row = connection.execute(
                    """
                    SELECT * FROM incidents
                    WHERE site = ? AND category = ? AND status != 'resolved'
                      AND identity_keys LIKE ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (event.site, category, f"%{identity}%"),
                ).fetchone()
            else:
                row = None
        return self._decode(row) if row is not None else None

    def create_incident(self, incident: Mapping[str, Any], event: NormalizedInput) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    id, title, status, severity, category, site, summary,
                    correlation_key, identity_keys, devices_json, evidence_json,
                    analysis_json, investigation_json, onsite_card_json, cc_reminder_json,
                    communication_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident["id"],
                    incident["title"],
                    incident["status"],
                    incident["severity"],
                    incident["category"],
                    incident["site"],
                    incident["summary"],
                    incident["correlation_key"],
                    incident["identity_keys"],
                    _dump(incident["devices"]),
                    _dump(incident["evidence"]),
                    _dump(incident["analysis"]),
                    _dump(incident["investigation"]),
                    _dump(incident["onsite_card"]),
                    _dump(incident["cc_reminder"]),
                    incident["communication_text"],
                    incident["created_at"],
                    incident["updated_at"],
                ),
            )
            self._insert_input(connection, incident["id"], event)
            self._audit(connection, incident["id"], "incident_created", {"source": event.source})
            self._audit_investigation(connection, incident["id"], incident["investigation"])
        result = self.get_incident(str(incident["id"]))
        assert result is not None
        return result

    def merge_incident(
        self,
        incident_id: str,
        update: Mapping[str, Any],
        event: NormalizedInput,
    ) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE incidents SET
                    title = ?, severity = ?, summary = ?, identity_keys = ?,
                    devices_json = ?, evidence_json = ?, analysis_json = ?,
                    investigation_json = ?, onsite_card_json = ?, cc_reminder_json = ?, communication_text = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    update["title"],
                    update["severity"],
                    update["summary"],
                    update["identity_keys"],
                    _dump(update["devices"]),
                    _dump(update["evidence"]),
                    _dump(update["analysis"]),
                    _dump(update["investigation"]),
                    _dump(update["onsite_card"]),
                    _dump(update["cc_reminder"]),
                    update["communication_text"],
                    update["updated_at"],
                    incident_id,
                ),
            )
            self._insert_input(connection, incident_id, event)
            self._audit(connection, incident_id, "evidence_merged", {"source": event.source})
            self._audit_investigation(connection, incident_id, update["investigation"])
        result = self.get_incident(incident_id)
        assert result is not None
        return result

    def update_status(self, incident_id: str, status: str) -> Optional[Dict[str, Any]]:
        if status not in {"new", "processing", "resolved"}:
            raise ValueError("status must be new, processing, or resolved")
        now = utc_now()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT status FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if current is None:
                return None
            connection.execute(
                "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, incident_id),
            )
            self._audit(
                connection,
                incident_id,
                "status_changed",
                {"from": current["status"], "to": status},
            )
        return self.get_incident(incident_id)

    def update_investigation(
        self,
        incident_id: str,
        investigation: Mapping[str, Any],
        audit_details: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now()
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if exists is None:
                return None
            connection.execute(
                "UPDATE incidents SET investigation_json = ?, updated_at = ? WHERE id = ?",
                (_dump(investigation), now, incident_id),
            )
            self._audit(
                connection,
                incident_id,
                "external_investigation_completed",
                dict(audit_details or {}),
            )
        return self.get_incident(incident_id)

    def _insert_input(
        self, connection: sqlite3.Connection, incident_id: str, event: NormalizedInput
    ) -> None:
        connection.execute(
            """
            INSERT INTO event_inputs (incident_id, source, event_time, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (incident_id, event.source, event.event_time, _dump(event.to_dict()), utc_now()),
        )

    @staticmethod
    def _audit_investigation(
        connection: sqlite3.Connection,
        incident_id: str,
        investigation: Mapping[str, Any],
    ) -> None:
        actions = (
            ("input_received", {"input_count": len(investigation.get("intake", []))}),
            ("fields_normalized", {"field_count": len(investigation.get("field_provenance", []))}),
            ("facts_extracted", {"fact_count": len(investigation.get("extracted_facts", []))}),
            ("rules_matched", {"rule_count": len(investigation.get("rule_matches", []))}),
            ("correlation_evaluated", dict(investigation.get("correlation", {}))),
            ("hypotheses_generated", {"hypothesis_count": len(investigation.get("hypotheses", []))}),
            ("conclusion_updated", dict(investigation.get("conclusion", {}))),
        )
        for action, details in actions:
            IncidentStore._audit(connection, incident_id, action, details)

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        incident_id: str,
        action: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (incident_id, action, details_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (incident_id, action, _dump(details), utc_now()),
        )
