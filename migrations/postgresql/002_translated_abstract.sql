ALTER TABLE paper
ADD COLUMN IF NOT EXISTS translated_abstract TEXT;

INSERT INTO schema_meta (key, value)
VALUES ('schema_version', '6')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;