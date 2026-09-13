from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260901090000_create_ingestion_tasks.sql"
)
ALLOW_RUNNING_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902090000_allow_running_ingestion_tasks.sql"
)
ADD_STARTED_AT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902093000_add_ingestion_task_started_at.sql"
)
ALLOW_TERMINAL_STATES_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902100000_allow_terminal_ingestion_task_states.sql"
)
ADD_RETRY_COUNT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902103000_add_ingestion_task_retry_count.sql"
)
ADD_ATTEMPT_ID_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902110000_add_ingestion_task_attempt_id.sql"
)
OUTBOX_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260902120000_create_ingestion_task_outbox.sql"
)
OUTBOX_LEASE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260907100000_add_outbox_publish_lease.sql"
)


def test_ingestion_task_migration_creates_minimal_pending_task_table() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE SCHEMA IF NOT EXISTS agent_core;" in sql
    assert "CREATE TABLE agent_core.ingestion_tasks" in sql
    assert "status VARCHAR(20) NOT NULL DEFAULT 'PENDING'" in sql
    assert "created_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql


def test_ingestion_task_migration_protects_id_and_current_status_boundary() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CONSTRAINT ingestion_tasks_id_not_blank" in sql
    assert "CHECK (btrim(id) <> '')" in sql
    assert "CONSTRAINT ingestion_tasks_status_valid" in sql
    assert "CHECK (status IN ('PENDING'))" in sql


def test_ingestion_task_migration_does_not_claim_future_task_states() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "RUNNING" not in sql
    assert "SUCCEEDED" not in sql
    assert "FAILED" not in sql
    assert "retry" not in sql.lower()


def test_allow_running_migration_atomically_replaces_status_constraint() -> None:
    sql = ALLOW_RUNNING_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "DROP CONSTRAINT ingestion_tasks_status_valid;" in sql
    assert "ADD CONSTRAINT ingestion_tasks_status_valid" in sql
    assert "CHECK (status IN ('PENDING', 'RUNNING'))" in sql


def test_allow_running_migration_does_not_claim_terminal_states_yet() -> None:
    sql = ALLOW_RUNNING_MIGRATION_PATH.read_text(encoding="utf-8")

    assert "SUCCEEDED" not in sql
    assert "FAILED" not in sql
    assert "retry" not in sql.lower()


def test_started_at_migration_adds_backward_compatible_nullable_column() -> None:
    sql = ADD_STARTED_AT_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "ADD COLUMN started_at TIMESTAMPTZ(3);" in sql
    assert "started_at TIMESTAMPTZ(3) NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_started_at_migration_does_not_change_task_states() -> None:
    sql = ADD_STARTED_AT_MIGRATION_PATH.read_text(encoding="utf-8")

    assert "DROP CONSTRAINT ingestion_tasks_status_valid" not in sql
    assert "SUCCEEDED" not in sql
    assert "FAILED" not in sql


def test_terminal_states_migration_atomically_replaces_status_constraint() -> None:
    sql = ALLOW_TERMINAL_STATES_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "DROP CONSTRAINT ingestion_tasks_status_valid;" in sql
    assert "ADD CONSTRAINT ingestion_tasks_status_valid" in sql
    for status in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED"):
        assert f"'{status}'" in sql


def test_terminal_states_migration_does_not_store_failure_details_or_retries() -> None:
    sql = ALLOW_TERMINAL_STATES_MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "error" not in sql
    assert "failure" not in sql
    assert "retry" not in sql


def test_retry_count_migration_adds_non_negative_default_counter() -> None:
    sql = ADD_RETRY_COUNT_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0" in sql
    assert "ingestion_tasks_retry_count_non_negative" in sql
    assert "CHECK (retry_count >= 0)" in sql


def test_retry_count_migration_does_not_add_retry_transition_or_new_states() -> None:
    sql = ADD_RETRY_COUNT_MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "set status" not in sql
    assert "pending" not in sql
    assert "running" not in sql
    assert "failed" not in sql


def test_attempt_id_migration_adds_backward_compatible_uuid_column() -> None:
    sql = ADD_ATTEMPT_ID_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "ADD COLUMN attempt_id UUID;" in sql
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_outbox_migration_creates_attempt_event_table() -> None:
    sql = OUTBOX_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE agent_core.ingestion_task_outbox" in sql
    assert "id UUID PRIMARY KEY" in sql
    assert "task_id VARCHAR(64) NOT NULL" in sql
    assert "attempt_id UUID NOT NULL" in sql
    assert "event_type VARCHAR(50) NOT NULL" in sql
    assert "payload JSONB NOT NULL" in sql
    assert "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql
    assert "published_at TIMESTAMPTZ NULL" in sql


def test_outbox_migration_has_attempt_scoped_idempotency_key() -> None:
    sql = OUTBOX_MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CONSTRAINT ingestion_task_outbox_event_unique" in sql
    assert "UNIQUE (task_id, event_type, attempt_id)" in sql


def test_outbox_lease_migration_adds_nullable_publisher_claim_fields() -> None:
    sql = OUTBOX_LEASE_MIGRATION_PATH.read_text(encoding="utf-8")

    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")
    assert "ALTER TABLE agent_core.ingestion_task_outbox" in sql
    assert "ADD COLUMN publish_attempt_id UUID" in sql
    assert "ADD COLUMN publishing_at TIMESTAMPTZ" in sql
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql
