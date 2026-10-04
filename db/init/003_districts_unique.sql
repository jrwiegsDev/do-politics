-- One row per district per map. Lets the loader upsert instead of duplicating.
ALTER TABLE districts
    ADD CONSTRAINT districts_identity_key
    UNIQUE (level, chamber, geoid, effective_from);
