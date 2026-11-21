FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para OCR (Tesseract + spa/eng) y PostgreSQL.
# Nota: se usa opencv-python-headless en requirements.txt (no requiere libGL/libglib).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Instala torch y torchvision CPU-only antes de requirements.txt para evitar
# arrastrar dependencias NVIDIA/CUDA (~4+ GB) desde PyPI.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.9.1
ARG TORCHVISION_VERSION=0.24.1

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
    --index-url "${TORCH_INDEX_URL}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Validaciones en build-time: garantiza build CPU sin CUDA y soporte de idioma español en Tesseract.
RUN python -c "import torch, torchvision; \
    assert torch.version.cuda is None, f'torch trae build CUDA: {torch.__version__}'; \
    print('torch', torch.__version__, '| torchvision', torchvision.__version__, '| sin CUDA: ok')" && \
    tesseract --version > /dev/null && \
    tesseract --list-langs | grep -q spa && \
    echo "tesseract listo con español"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf-cache

# Crea directorios de cache/montaje y asigna usuario no-root.
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /opt/hf-cache /tickets && \
    chown -R appuser:appuser /app /opt/hf-cache /tickets

USER appuser

EXPOSE 8000
