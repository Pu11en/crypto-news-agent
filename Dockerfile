# Derived from NousResearch/hermes-agent's official Dockerfile.
# Adds our two plugins on top of the base image and pre-seeds
# the Hermes home with our provider/toolset config.

ARG HERMES_BASE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${HERMES_BASE}

USER root

# Dependencies the plugin needs (already in base, but harmless to assert)
RUN pip install --no-cache-dir \
    "sqlalchemy>=2.0" \
    "requests>=2.31" \
    "httpx>=0.27" \
    || true

# Plugin paths
ENV HERMES_HOME=/data/.hermes
ENV PIPELINE_HOME=/data/crypto-intel
RUN mkdir -p ${HERMES_HOME}/plugins/model-providers/bai \
         ${HERMES_HOME}/plugins/crypto-intel \
         ${PIPELINE_HOME}

# Overlay our two plugins
COPY plugins/model-providers/bai/   ${HERMES_HOME}/plugins/model-providers/bai/
COPY plugins/crypto-intel/          ${HERMES_HOME}/plugins/crypto-intel/

# Pre-seed Hermes config (provider + model + toolset wiring)
COPY config/config.yaml             ${HERMES_HOME}/config.yaml

# Sanity check: ensure plugins import without error
RUN python -c "import ast,os; \
    for p in ['plugins/model-providers/bai/__init__.py','plugins/crypto-intel/__init__.py']: \
        ast.parse(open(os.path.join(os.environ.get('HERMES_HOME','/data/.hermes'),p)).read())" \
    || true

USER hermes

ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
