-- Operator-attributed revocation evidence for reference entitlements.

ALTER TABLE reference_data_entitlements
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS revocation_reason VARCHAR(500);

DROP TRIGGER IF EXISTS trg_reference_data_entitlement_immutable
    ON reference_data_entitlements;

UPDATE reference_data_entitlements
SET
    revoked_at = updated_at,
    revoked_by = 'migration:schema-030',
    revocation_reason = (
        'Legacy revocation migrated without operator-attributed reason.'
    )
WHERE status = 'REVOKED'
  AND (
      revoked_at IS NULL
      OR revoked_by IS NULL
      OR revocation_reason IS NULL
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_reference_entitlement_revocation_audit'
          AND conrelid = 'reference_data_entitlements'::regclass
    ) THEN
        ALTER TABLE reference_data_entitlements
            ADD CONSTRAINT ck_reference_entitlement_revocation_audit
            CHECK (
                (
                    status = 'REVOKED'
                    AND revoked_at IS NOT NULL
                    AND revoked_by IS NOT NULL
                    AND revocation_reason IS NOT NULL
                    AND revoked_at >= entitlement_verified_at
                    AND LENGTH(BTRIM(revoked_by)) BETWEEN 1 AND 100
                    AND LENGTH(BTRIM(revocation_reason)) BETWEEN 1 AND 500
                )
                OR (
                    status != 'REVOKED'
                    AND revoked_at IS NULL
                    AND revoked_by IS NULL
                    AND revocation_reason IS NULL
                )
            );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_reference_data_entitlement_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'reference-data entitlement evidence is append-only';
    END IF;
    IF (
        OLD.status = 'VALIDATED'
        AND NEW.status = 'REVOKED'
        AND NEW.revoked_at IS NOT NULL
        AND NEW.revoked_at <= NOW() + INTERVAL '5 minutes'
        AND NEW.revoked_by ~ '^operator:[A-Za-z0-9._-]{1,80}$'
        AND LENGTH(BTRIM(NEW.revocation_reason)) BETWEEN 1 AND 500
        AND (
            to_jsonb(NEW)
            - 'status'
            - 'updated_at'
            - 'revoked_at'
            - 'revoked_by'
            - 'revocation_reason'
        ) = (
            to_jsonb(OLD)
            - 'status'
            - 'updated_at'
            - 'revoked_at'
            - 'revoked_by'
            - 'revocation_reason'
        )
    ) THEN
        NEW.updated_at := NOW();
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'reference-data entitlement evidence is append-only';
END;
$$;

CREATE TRIGGER trg_reference_data_entitlement_immutable
BEFORE UPDATE OR DELETE ON reference_data_entitlements
FOR EACH ROW
EXECUTE FUNCTION guard_reference_data_entitlement_change();
