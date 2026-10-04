FROM postgres:18

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-$PG_MAJOR-postgis-3 \
        postgresql-$PG_MAJOR-postgis-3-scripts \
        postgis \
    && rm -rf /var/lib/apt/lists/*
