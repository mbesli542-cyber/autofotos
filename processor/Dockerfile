# AutoExperten processor (FastAPI + ONNX Runtime, CPU only).
#
# The build context is the REPOSITORY ROOT, because the showroom preset and
# the official logo live in the Next.js public/ folder:
#
#   docker build -f processor/Dockerfile -t autoexperten-processor .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file processor/.env autoexperten-processor
#
# processor/.env holds PROCESSOR_API_KEY (template: processor/.env.example), so the
# key never appears on the command line; `-e PROCESSOR_API_KEY` without a value
# takes it from the calling shell's environment instead.
#
# The container refuses to start without PROCESSOR_API_KEY
# (PROCESSOR_REQUIRE_API_KEY=true below); only for a local test without a key
# add -e PROCESSOR_REQUIRE_API_KEY=false. Publish the port on 127.0.0.1 unless
# other machines must reach it (Docker publishes on all interfaces otherwise,
# past the host firewall).
#
# The segmentation model and the vehicle detector are downloaded and SHA-256
# verified at build time, so the container needs no internet access at runtime.

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
ARG DETECTOR_MODEL=rf-detr-seg-medium
ENV PROCESSOR_SEGMENTATION_MODEL=${SEGMENTATION_MODEL} \
    PROCESSOR_DETECTOR_MODEL=${DETECTOR_MODEL}
# Segmentation model (~220 MB) + vehicle detector (~130 MB), both SHA-256 verified;
# the BuildKit cache keeps them between builds when only code changes.
RUN --mount=type=cache,target=/root/.cache/ae-models \
    PROCESSOR_MODELS_DIR=/root/.cache/ae-models python scripts/download_models.py \
    && mkdir -p /app/models \
    && cp "/root/.cache/ae-models/${SEGMENTATION_MODEL}.onnx" "/root/.cache/ae-models/${DETECTOR_MODEL}.onnx" /app/models/ \
    && chmod 0644 /app/models/*.onnx

# Showroom preset + 3D showroom plates (presets/autoexperten-standard/) and the
# official branding (replace the files in public/ and rebuild to update them,
# or mount a folder at /app/assets).
COPY public/presets ./assets/presets
COPY public/brand ./assets/brand

# PROCESSOR_REQUIRE_API_KEY=true: no start without PROCESSOR_API_KEY (app/main.py create_app).
ENV PROCESSOR_ASSETS_DIR=/app/assets \
    PROCESSOR_MODELS_DIR=/app/models \
    PROCESSOR_DATA_DIR=/data \
    PROCESSOR_AUTO_DOWNLOAD_MODELS=false \
    PROCESSOR_REQUIRE_API_KEY=true

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin processor \
    && mkdir -p /data && chown processor /data
USER processor
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
