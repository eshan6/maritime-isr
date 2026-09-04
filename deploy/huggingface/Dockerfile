# Maritime ISR — read-only demo API for a Hugging Face Docker Space.
#
# WHAT THIS SERVES
# ----------------
# A frozen snapshot of the synthetic corpus. The API does no writes at serve
# time (verified: no land_table, no writer, no open(...,"w") anywhere under
# maritime_isr/api/), which is the whole reason this can live on an ephemeral
# container filesystem at all.
#
# WHAT THIS IS NOT
# ----------------
# It is not the pipeline. SNAP/pyroSAR preprocessing, live AIS capture and
# corpus generation are NOT run here and cannot be: they need a real host with
# persistent disk and far more RAM. This image ships the *output* of a local
# run. When the corpus changes on the laptop, this image has to be rebuilt --
# nothing here regenerates itself.
#
# PORT
# ----
# Hugging Face Spaces routes to port 7860 and permits no custom mapping, so
# MISR_API_PORT is pinned to it. MISR_API_HOST must be 0.0.0.0 rather than the
# 127.0.0.1 the laptop entrypoint defaults to, or the container answers only
# itself and the Space shows a blank page.

FROM python:3.11-slim

# git: some transitive installs still shell out to it. No compiler is installed
# on purpose -- every dependency here (pyarrow, duckdb, shapely, scikit-image,
# scikit-learn, h3) ships manylinux wheels, and if that ever stops being true
# the build should fail loudly here rather than silently pull in a toolchain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000. Anything the process needs to write --
# and that is only ever scratch, never project data -- has to be owned by it.
RUN useradd -m -u 1000 misr
USER misr
ENV HOME=/home/misr \
    PATH=/home/misr/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /home/misr/app

# Dependencies first, so editing code or swapping the corpus does not reinstall
# the world on every rebuild.
COPY --chown=misr:misr pyproject.toml README.md ./
COPY --chown=misr:misr maritime_isr/__init__.py ./maritime_isr/__init__.py
RUN pip install --no-cache-dir --user -e ".[api,pans]"

COPY --chown=misr:misr maritime_isr/ ./maritime_isr/

# The built React bundle. The API mounts it if present (api/app.py
# _mount_frontend), so this image serves the whole demo on its own -- useful as
# a fallback if Vercel is unreachable, and it makes the Space independently
# checkable without a second deploy.
COPY --chown=misr:misr frontend/dist/ ./frontend/dist/

# The corpus. ~145 MB. This is the slowest layer to push and the one that
# changes most often, so it is deliberately last.
COPY --chown=misr:misr data/ ./data/

ENV MISR_API_HOST=0.0.0.0 \
    MISR_API_PORT=7860 \
    MISR_STORE_BACKEND=local \
    MISR_DATA_ROOT=/home/misr/app/data

EXPOSE 7860

# A cold Space start is container pull + this process. The app itself is quick
# (~8 s to "Application startup complete" on a 2-vCPU box); the pull dominates.
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7860/api/health',timeout=4)" || exit 1

CMD ["python", "-m", "maritime_isr.api"]
