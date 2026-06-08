# Caliper — base image (the brain + web app + control layer).
#
# This image runs the agent, trust layer, and web UI. It does NOT bundle the heavy
# domain tools (salmon, STAR, …) — those come from a per-pack environment layered on
# top, or are dispatched to a remote compute host. For a self-contained lab image,
# add a domain layer that conda-installs the pack's tools.
FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e ".[web,llm]"

# Confined workspace + read-only data root are provided at runtime via env / mounts:
#   docker run -e ANTHROPIC_API_KEY=... -e CALIPER_WEB_PASSWORD=... \
#     -e CALIPER_WORKSPACE=/work -e CALIPER_DATA_ROOT=/data \
#     -v /work:/work -v /data:/data:ro -p 8000:8000 caliper
ENV CALIPER_WEB_HOST=0.0.0.0 CALIPER_WEB_PORT=8000 \
    CALIPER_WORKSPACE=/work CALIPER_PROVIDER=anthropic
EXPOSE 8000
CMD ["caliper-web"]
