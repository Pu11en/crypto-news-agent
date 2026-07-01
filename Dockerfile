# Derived from NousResearch/hermes-agent.
# Builds hermes-agent from source (no pre-built image needed),
# then overlays our two plugins and pre-seeds the Hermes config.

FROM python:3.12-slim

USER root

# System deps needed by hermes-agent and our plugins
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        gcc \
        libffi-dev \
        libssl-dev \
        && rm -rf /var/lib/apt/lists/*

# Clone hermes-agent and install it
RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent
WORKDIR /opt/hermes-agent
RUN pip install --no-cache-dir -e .

# Plugin dependencies
RUN pip install --no-cache-dir \
        "sqlalchemy>=2.0" \
        "requests>=2.31" \
        "httpx>=0.27"

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

# Sanity check: ensure plugin files at least parse
RUN python -c "
import ast, os
home = os.environ.get('HERMES_HOME', '/data/.hermes')
for p in ['plugins/model-providers/bai/__init__.py', 'plugins/crypto-intel/__init__.py']:
    full = os.path.join(home, p)
    if os.path.exists(full):
        ast.parse(open(full).read())
        print(f'OK {p}')
    else:
        print(f'WARN missing {p}')
"

ENTRYPOINT ["python", "-m", "hermes_agent"]
