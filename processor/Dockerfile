# AutoExperten processor (FastAPI + ONNX Runtime, CPU only).
#
# The build context is the REPOSITORY ROOT, because the showroom preset and
# the official logo live in the Next.js public/ folder:
#
#   docker build -f processor/Dockerfile -t autoexperten-processor .
#   docker run --rm -p 8000:8000 --env-file processor/.env autoexperten-processor
#
# The segmentation model is downloaded and SHA-256 verified at build time,
# so the container needs no internet access at runtime.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY processor/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY processor/app ./app
COPY processor/scripts ./scripts

ARG SEGMENTATION_MODEL=birefnet-general-lite
ENV PROCESSOR_SEGMENTATION_MODEL=${SEGMENTATION_MODEL}
# BuildKit cache keeps the ~220 MB model between builds when only code changes.
RUN --mount=type=cache,target=/root/.cache/ae-models \
    PROCESSOR_MODELS_DIR=/root/.cache/ae-models python scripts/download_models.py \
    && mkdir -p /app/models \
    && cp "/root/.cache/ae-models/${SEGMENTATION_MODEL}.onnx" /app/models/ \
    && chmod 0644 /app/models/*.onnx

# Showroom preset + 3D showroom plates (presets/autoexperten-standard/) and the
# official branding (replace the files in public/ and rebuild to update them,
# or mount a folder at /app/assets).
COPY public/presets ./assets/presets
COPY public/brand ./assets/brand

ENV PROCESSOR_ASSETS_DIR=/app/assets \
    PROCESSOR_MODELS_DIR=/app/models \
    PROCESSOR_DATA_DIR=/data \
    PROCESSOR_AUTO_DOWNLOAD_MODELS=false

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin processor \
    && mkdir -p /data && chown processor /data
USER processor
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
