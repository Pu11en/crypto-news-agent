#!/bin/sh
set -eu

mkdir -p /data/video-jobs

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FATAL: required command is missing: $1" >&2
    exit 1
  }
}

for command_name in python node npm ffmpeg ffprobe chromium hyperframes; do
  require_command "$command_name"
done

if [ "$(hyperframes --version)" != "0.7.76" ]; then
  echo "FATAL: HyperFrames 0.7.76 is required" >&2
  exit 1
fi

WHISPER_BIN="${HYPERFRAMES_WHISPER_PATH:-$HOME/.cache/hyperframes/whisper/whisper.cpp/build/bin/whisper-cli}"
WHISPER_MODEL="$HOME/.cache/hyperframes/whisper/models/ggml-${WHISPER_MODEL:-small.en}.bin"
if [ ! -x "$WHISPER_BIN" ]; then
  echo "FATAL: baked whisper.cpp binary is missing" >&2
  exit 1
fi
if [ ! -s "$WHISPER_MODEL" ]; then
  echo "FATAL: baked Whisper model is missing: $WHISPER_MODEL" >&2
  exit 1
fi

printf 'Runtime ready: Node %s, HyperFrames %s, FFmpeg and Chromium available\n' \
  "$(node --version)" "$(hyperframes --version)"
exec python src/bot.py
