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
#
# SAM 2.1 (prompted segmentation, app/pipeline/sam2.py) has no official ONNX
# release: its two graphs are BUILT in the separate stage below from the pinned
# Hugging Face checkpoint with the pinned torch/transformers stack
# (scripts/export_sam2.py) and only the ONNX files are copied into the image –
# no torch in the final image. Opt-in while no pipeline feature uses it:
#
#   docker build -f processor/Dockerfile --build-arg SAM2_MODEL=sam2.1-hiera-small -t autoexperten-processor .
#
# The build verifies the built graphs against the SHA-256 pinned in
# SAM2_REGISTRY (export is byte-deterministic with the pinned stack; a mismatch
# fails the build with the fingerprint of what was built – see processor/README.md).

ARG SAM2_MODEL=""

# ---------------------------------------------------------------- SAM 2.1 export stage
FROM python:3.13-slim AS sam2-export
ARG SAM2_MODEL
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_DISABLE_TELEMETRY=1
WORKDIR /export
COPY processor/scripts/export_sam2.py ./scripts/export_sam2.py
# pins = PINNED_VERSIONS in scripts/export_sam2.py (the script refuses other versions);
# the pip and checkpoint caches survive between builds. Empty SAM2_MODEL: nothing is built.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/huggingface \
    mkdir -p /export/models && if [ -n "$SAM2_MODEL" ]; then \
      pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0+cpu \
      && pip install transformers==5.22.1 onnx==1.23.2 onnxscript==0.7.2 onnxruntime==1.31.1 \
           huggingface_hub==2.4.0 numpy==2.5.4 pillow==13.0.0 \
      && HF_HUB_CACHE=/root/.cache/huggingface python scripts/export_sam2.py --model "$SAM2_MODEL" --out /export/models \
      && rm -f /export/models/*.export.json; fi

# ---------------------------------------------------------------- service image
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

# SAM 2.1 graphs built above (none without --build-arg SAM2_MODEL), verified against
# the pinned SHA-256 like the downloaded models (PROCESSOR_SAM2_MODEL selects them).
ARG SAM2_MODEL
COPY --from=sam2-export /export/models/ /app/models/
RUN if [ -n "$SAM2_MODEL" ]; then \
      chmod 0644 /app/models/*.onnx \
      && PROCESSOR_MODELS_DIR=/app/models python scripts/download_models.py "$SAM2_MODEL"; fi
ENV PROCESSOR_SAM2_MODEL=${SAM2_MODEL:-sam2.1-hiera-small}

# Showroom preset + the dealer studio plate set (presets/autoexperten-haendler/) and the
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
