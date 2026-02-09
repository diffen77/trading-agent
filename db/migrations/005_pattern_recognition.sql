-- Migration 005: Pattern recognition columns
ALTER TABLE technical_signals ADD COLUMN IF NOT EXISTS pattern VARCHAR(100);
ALTER TABLE technical_signals ADD COLUMN IF NOT EXISTS pattern_signal VARCHAR(20);
