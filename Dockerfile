# The daily run, packaged: python -m newsletter_video.cloud_run
# Build for Cloud Run (amd64):  gcloud builds submit --tag <region>-docker.pkg.dev/<project>/<repo>/daily
# Build for a local check:      docker build -t bytesize-daily .
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/root \
    WHISPER_BACKEND=faster \
    NOTIFY_MACOS=0

# ffmpeg for audio/video, and the shared libraries headless Chrome needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl unzip ca-certificates fontconfig fonts-liberation \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libxext6 \
        libxshmfence1 libglib2.0-0 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (HyperFrames needs >=22), copied from the official image.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /app
COPY requirements.txt requirements-cloud.txt ./
RUN pip install -r requirements-cloud.txt

# Everything the run downloads on a first start is baked in, so a job never waits on (or breaks over) the network:
# the HyperFrames CLI and its Chrome, the Kokoro voice, and the Whisper model that times the captions.
ARG HYPERFRAMES_VERSION=0.8.141
RUN timeout 900 npx --yes hyperframes@${HYPERFRAMES_VERSION} browser ensure
RUN mkdir -p /root/.cache/hyperframes/tts/models /root/.cache/hyperframes/tts/voices \
    && curl -fL -o /root/.cache/hyperframes/tts/models/kokoro-v1.0.onnx \
        https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
    && curl -fL -o /root/.cache/hyperframes/tts/voices/voices-v1.0.bin \
        https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', device='cpu', compute_type='int8')"

COPY . .

ENTRYPOINT ["python", "-m", "newsletter_video.cloud_run"]
