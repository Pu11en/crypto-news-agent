# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS runtime-base

ARG HYPERFRAMES_VERSION=0.7.76

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    HYPERFRAMES_BIN=/usr/local/bin/hyperframes \
    HYPERFRAMES_WHISPER_PATH=/root/.cache/hyperframes/whisper/whisper.cpp/build/bin/whisper-cli \
    HYPERFRAMES_EXTRACT_CACHE_DIR=/data/hyperframes-cache \
    PRODUCER_LOW_MEMORY_MODE=1 \
    VIDEO_WORK_DIR=/data/video-jobs \
    HOME=/root

# Copy the official Node 22 runtime and npm tooling into the Python base.
COPY --from=node-runtime /usr/local/bin/ /usr/local/bin/
COPY --from=node-runtime /usr/local/lib/node_modules/ /usr/local/lib/node_modules/

# Runtime media stack. Chromium is the shared browser for HyperFrames and
# the browser-backed DOM overflow QA.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        ffmpeg \
        fonts-dejavu-core \
        fonts-liberation \
        libgomp1 \
        unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install one exact CLI version. The application invokes this baked binary,
# so live jobs never download npm packages.
RUN npm install --global --omit=dev "hyperframes@${HYPERFRAMES_VERSION}" \
    && test "$(hyperframes --version)" = "${HYPERFRAMES_VERSION}" \
    && npm cache clean --force

# Build whisper.cpp and download the production model in a disposable stage.
# Only the resulting cache is copied into the final image.
FROM runtime-base AS whisper-builder
ARG WHISPER_CPP_VERSION=v1.8.6
ARG WHISPER_MODEL_REVISION=c521a4b02f422512d734391fdf08bb08c0862f68
ARG WHISPER_MODEL_SHA256=c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --branch "${WHISPER_CPP_VERSION}" --depth 1 \
        https://github.com/ggml-org/whisper.cpp.git /tmp/whisper.cpp \
    && cmake -S /tmp/whisper.cpp -B /tmp/whisper.cpp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
    && cmake --build /tmp/whisper.cpp/build \
        --config Release --target whisper-cli -j2 \
    && mkdir -p \
        /root/.cache/hyperframes/whisper/whisper.cpp/build/bin \
        /root/.cache/hyperframes/whisper/models \
    && cp /tmp/whisper.cpp/build/bin/whisper-cli \
        /root/.cache/hyperframes/whisper/whisper.cpp/build/bin/whisper-cli \
    && curl --fail --location --retry 3 \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/${WHISPER_MODEL_REVISION}/ggml-small.en.bin?download=true" \
        --output /root/.cache/hyperframes/whisper/models/ggml-small.en.bin \
    && echo "${WHISPER_MODEL_SHA256}  /root/.cache/hyperframes/whisper/models/ggml-small.en.bin" \
        | sha256sum --check --strict \
    && /root/.cache/hyperframes/whisper/whisper.cpp/build/bin/whisper-cli --help \
        >/dev/null \
    && rm -rf /tmp/whisper.cpp

FROM runtime-base AS production

ENV HYPERFRAMES_NO_UPDATE_CHECK=1 \
    HYPERFRAMES_NO_AUTO_INSTALL=1 \
    HYPERFRAMES_NO_TELEMETRY=1 \
    HYPERFRAMES_BROWSER_PATH=/usr/bin/chromium

COPY --from=whisper-builder /root/.cache/hyperframes/whisper/ /root/.cache/hyperframes/whisper/

COPY accounts.txt ./
COPY src/ ./src/
COPY scripts/container-entrypoint.sh /usr/local/bin/jordancrypto-entrypoint

RUN chmod +x /usr/local/bin/jordancrypto-entrypoint \
    && mkdir -p /data/reports /data/video-jobs /data/hyperframes-cache \
    && python -m compileall -q src \
    && chromium --version \
    && ffmpeg -version | head -n 1 \
    && hyperframes --version

CMD ["jordancrypto-entrypoint"]
