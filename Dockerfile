FROM python:3.11-slim

WORKDIR /app

# System dependencies for AI/ML packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Torch CPU-only ANTES que el resto de dependencias.
# Sin esto, pip descarga ~4.1GB de paquetes nvidia-* + triton que son inservibles
# en linux/arm64 (Docker Desktop no expone GPU NVIDIA) y que ocupan espacio y RAM.
# El codigo ya resuelve solo el device: device="cuda" if torch.cuda.is_available() else "cpu"
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.9.1
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}" --index-url "${TORCH_INDEX_URL}" && \
    pip install --no-cache-dir --upgrade pip

# Resto de dependencias. torch ya cumple "torch>=2.3.0" de requirements.txt,
# asi que pip lo deja intacto y no vuelve a bajar la version con CUDA.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf-cache

# Cache de HuggingFace en volumen persistente: sin esto, el modelo de embeddings
# (~470MB) se vuelve a descargar cada vez que se recrea el contenedor.
RUN mkdir -p /opt/hf-cache

EXPOSE 8000
