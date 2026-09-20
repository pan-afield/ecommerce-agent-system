-- 状态变化与审计、补偿任务在同一事务中提交；不能先更新状态再异步补审计。
CREATE TABLE "refund_audit_events" (
    "id" BIGSERIAL PRIMARY KEY,
    "execution_id" VARCHAR(64) NOT NULL REFERENCES "refund_executions"("id") ON DELETE RESTRICT,
    "action" VARCHAR(32) NOT NULL,
    "source" VARCHAR(32) NOT NULL,
    "actor_user_id" VARCHAR(64),
    "source_event_id" VARCHAR(128),
    "from_status" VARCHAR(32),
    "to_status" VARCHAR(32),
    "provider_reference" VARCHAR(128),
    "error_code" VARCHAR(64),
    "note" VARCHAR(500),
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX "refund_audit_events_execution_id_id_idx" ON "refund_audit_events"("execution_id", "id");

CREATE TABLE "refund_recovery_jobs" (
    "execution_id" VARCHAR(64) PRIMARY KEY REFERENCES "refund_executions"("id") ON DELETE RESTRICT,
    "status" VARCHAR(32) NOT NULL DEFAULT 'READY',
    "attempts" INTEGER NOT NULL DEFAULT 0 CHECK ("attempts" >= 0),
    "next_attempt_at" TIMESTAMPTZ(3) NOT NULL DEFAULT clock_timestamp(),
    "lease_token" UUID,
    "lease_expires_at" TIMESTAMPTZ(3),
    "last_error_code" VARCHAR(64),
    "updated_at" TIMESTAMPTZ(3) NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT "refund_recovery_status_check" CHECK ("status" IN ('READY','LEASED','COMPLETED','MANUAL_REQUIRED')),
    CONSTRAINT "refund_recovery_lease_check" CHECK (
        ("status" = 'LEASED' AND "lease_token" IS NOT NULL AND "lease_expires_at" IS NOT NULL)
        OR ("status" <> 'LEASED' AND "lease_token" IS NULL AND "lease_expires_at" IS NULL)
    )
);
CREATE INDEX "refund_recovery_jobs_status_next_attempt_at_idx" ON "refund_recovery_jobs"("status", "next_attempt_at");

-- 旧记录只标为迁移时快照，不伪造过去发生过的状态轨迹。
INSERT INTO "refund_audit_events" ("execution_id","action","source","to_status","provider_reference")
SELECT "id", 'BASELINE', 'migration', "status", "provider_reference" FROM "refund_executions";
INSERT INTO "refund_recovery_jobs" ("execution_id")
SELECT "id" FROM "refund_executions" WHERE "status" IN ('RUNNING','PROCESSING');

CREATE FUNCTION refund_capture_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    context jsonb := COALESCE(NULLIF(current_setting('app.refund_audit_context', true), ''), '{}')::jsonb;
    previous_status text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status = NEW.status AND OLD.provider_reference IS NOT DISTINCT FROM NEW.provider_reference THEN
            RETURN NEW;
        END IF;
        previous_status := OLD.status;
    END IF;
    EXECUTE format('INSERT INTO %I.refund_audit_events
        (execution_id,action,source,actor_user_id,source_event_id,from_status,to_status,provider_reference)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)', TG_TABLE_SCHEMA)
    USING NEW.id, CASE WHEN TG_OP = 'INSERT' THEN 'CREATED' ELSE 'STATE_CHANGED' END,
          COALESCE(context->>'source','database'), context->>'actor_user_id',
          context->>'source_event_id', previous_status, NEW.status, NEW.provider_reference;

    IF NEW.status IN ('RUNNING','PROCESSING') THEN
        EXECUTE format('INSERT INTO %I.refund_recovery_jobs (execution_id,next_attempt_at)
            VALUES ($1,clock_timestamp() + interval ''60 seconds'')
            ON CONFLICT (execution_id) DO NOTHING', TG_TABLE_SCHEMA) USING NEW.id;
    ELSIF NEW.status IN ('SUCCEEDED','FAILED') THEN
        EXECUTE format('UPDATE %I.refund_recovery_jobs SET status=''COMPLETED'',
            lease_token=NULL,lease_expires_at=NULL,last_error_code=NULL,updated_at=clock_timestamp()
            WHERE execution_id=$1 AND NOT
              (status=''MANUAL_REQUIRED'' AND last_error_code IS NOT DISTINCT FROM ''TERMINAL_CONFLICT'')',
            TG_TABLE_SCHEMA) USING NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER refund_execution_audit AFTER INSERT OR UPDATE ON "refund_executions"
FOR EACH ROW EXECUTE FUNCTION refund_capture_transition();

CREATE FUNCTION refund_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'refund audit events are append-only';
END;
$$;
CREATE TRIGGER refund_audit_no_changes BEFORE UPDATE OR DELETE ON "refund_audit_events"
FOR EACH ROW EXECUTE FUNCTION refund_audit_append_only();
CREATE TRIGGER refund_audit_no_truncate BEFORE TRUNCATE ON "refund_audit_events"
FOR EACH STATEMENT EXECUTE FUNCTION refund_audit_append_only();
