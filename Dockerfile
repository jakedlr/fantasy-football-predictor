# The home machine's Expected Points scheduler (S11.3, ADR 0006): `ffp schedule` runs `ffp slate` then
# `ffp push` on game days. Modelled on prizepicks-optimizer's image. Runs from the user's machine because
# DraftKings and FanDuel refuse datacenter IPs.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    NFLREADPY_CACHE=filesystem NFLREADPY_CACHE_DIR=/data/nflreadpy \
    FFP_SCHEMA=/app/site/schema/expected-points.schema.json
WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .
# ffp push checks every push against the site's schema before sending it.
COPY site/schema/expected-points.schema.json site/schema/
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
# /data holds the nflverse and HTTP caches, snapshots and the latest expected-points.json; the
# working directory is there so ffp's relative paths (.cache/, snapshots/) land in the volume.
VOLUME /data
WORKDIR /data
CMD ["ffp", "schedule", "--output-dir", "/data/out"]
