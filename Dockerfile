FROM python:3.11-slim

WORKDIR /app

# System dependencies for AI/ML packages
#
# El bloque de tesseract NO es opcional si se quiere OCR dentro del contenedor.
# pytesseract es el envoltorio de Python y no incluye el motor: sin
# `tesseract-ocr` aqui, el contenedor levanta bien, el escaner corre, y cada
# foto falla con un error que solo aparece en el log. Por eso se instala el
# motor y el paquete de espanol en la MISMA capa que el envoltorio, y por eso
# `GET /api/v1/scan/ocr` existe: para poder preguntar en vez de adivinar.
#
# `tesseract-ocr-spa` es el idioma. Sin el, Tesseract lee los tickets en ingles
# y los numeros se leen distinto, que en un comprobante es exactamente donde no
# puede haber errores.
#
# `libgl1` y `libglib2.0-0` NO se instalan todavia: el preprocesado del OCR usa
# solo Pillow. Si se agrega opencv-python mas adelante (el codigo lo soporta
# pero no se declara, a proposito), estos dos son los que hacen que
# `import cv2` no reviente en un slim. Anotado para que el proximo que lo
# agregue no lo descubra en produccion.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Torch CPU-only ANTES que el resto de dependencias.
# Sin esto, pip descarga ~4.1GB de paquetes nvidia-* + triton que son inservibles
# en linux/arm64 (Docker Desktop no expone GPU NVIDIA) y que ocupan espacio y RAM.
# El codigo ya resuelve solo el device: device="cuda" if torch.cuda.is_available() else "cpu"
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.9.1
# `torchvision` va aqui y no en `requirements.txt`, por el mismo motivo que
# torch: tiene que salir del indice CPU.
#
# `easyocr` depende de `torchvision`, y `torchvision` fija una version EXACTA de
# torch. Si torchvision llegara por `requirements.txt`, pip lo resolveria desde
# PyPI, cuya build de `torch` por omision trae CUDA — y como la version fijada no
# coincidiria, pip reinstalaria torch. El resultado seria la imagen de 2 GB
# convertida en ~4.5 GB de paquetes `nvidia-*` que en linux/arm64 no sirven para
# nada, que es justo lo que la linea 33 dice evitar.
#
# La pareja se verifico en PyPI, no se adivino: `torchvision 0.24.1` declara
# `torch==2.9.1`, que es exactamente el `TORCH_VERSION` de arriba. Si se cambia
# uno hay que cambiar el otro, y el build falla en vez de dejar una imagen 2.5 GB
# mas grande y lenta.
ARG TORCHVISION_VERSION=0.24.1
RUN pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url "${TORCH_INDEX_URL}" && \
    pip install --no-cache-dir --upgrade pip

# Resto de dependencias. torch y torchvision ya cumplen lo que piden, asi que
# pip los deja intactos y no vuelve a bajar la version con CUDA.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El build falla aqui si easyocr trajo una build de CUDA por la puerta de atras.
# Es una comprobacion de una linea que convierte un error de 4.5 GB en un error
# de build: el sintoma (una imagen que pesa el doble) aparece DESPUES, cuando ya
# se pagaron la descarga y el tiempo.
#
# Lo que se comprueba es `torch.version.cuda is None`, NO el `+cpu` del
# `__version__`. La primera version de esta linea buscaba `'+cpu'` en las dos
# versiones y el build fallo con `AssertionError: 0.24.1`: `torchvision` no
# lleva sufijo de build en el `__version__` aunque venga del indice CPU.
#
# `torch.version.cuda` si es la pregunta correcta: es `None` en la build CPU y
# una cadena de tipo `'12.1'` en la de CUDA. Es la propiedad que decide si hay
# paquetes nvidia-* detrás, no la-etiqueta.
RUN python -c "import torch, torchvision; \
    assert torch.version.cuda is None, f'torch trae build CUDA: {torch.__version__}'; \
    print('torch', torch.__version__, '| torchvision', torchvision.__version__, '| sin CUDA: ok')"

# Se comprueba aqui y no en el arranque de la app. Si el binario faltara, el
# build falla en el CI, que es donde se puede actuar, y no a las 3 de la tarde
# cuando alguien sube la primera foto.
RUN tesseract --version > /dev/null && \
    tesseract --list-langs | grep -q spa && \
    echo "tesseract listo con español"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf-cache

# Cache de HuggingFace en volumen persistente: sin esto, el modelo de embeddings
# (~470MB) se vuelve a descargar cada vez que se recrea el contenedor.
RUN mkdir -p /opt/hf-cache

# Punto de montaje de la carpeta de tickets. El host la monta en `/tickets` en
# solo lectura (ver docker-compose.yml).
#
# Este `mkdir` no crea la carpeta del host: si el bind mount no existe, Docker
# crea un DIRECTORIO VACIO en el host con el nombre de la ruta, y el operador
# no se da cuenta hasta que el escaner dice "0 archivos vistos" y cree que la
# carpeta esta vacia. Por eso la app valida la carpeta al arrancar y avisa con su
# ruta efectiva en `GET /api/v1/scan/config`.
RUN mkdir -p /tickets

EXPOSE 8000
