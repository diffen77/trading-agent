ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS sector_source VARCHAR(100),
    ADD COLUMN IF NOT EXISTS sector_verified_at TIMESTAMPTZ;

ALTER TABLE companies
    DROP CONSTRAINT IF EXISTS companies_sector_provenance_pair;

ALTER TABLE companies
    ADD CONSTRAINT companies_sector_provenance_pair CHECK (
        (sector_source IS NULL) = (sector_verified_at IS NULL)
    );

CREATE INDEX IF NOT EXISTS idx_companies_sector_source
    ON companies (sector_source, sector_verified_at);
