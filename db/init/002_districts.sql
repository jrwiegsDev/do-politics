CREATE TABLE districts (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level           text NOT NULL,     -- 'federal'
    chamber         text NOT NULL,     -- 'house'
    state_fips      char(2) NOT NULL,  -- '17'
    code            text NOT NULL,     -- '13'
    geoid           text NOT NULL,     -- '1713'
    name            text NOT NULL,     -- 'Congressional District 13'
    effective_from  date NOT NULL,
    effective_to    date,              -- NULL means still in effect
    source          text NOT NULL,     -- 'tl_2025_17_cd119'
    boundary        geometry(MultiPolygon, 4269) NOT NULL
);

CREATE INDEX districts_boundary_gix ON districts USING GIST (boundary);
