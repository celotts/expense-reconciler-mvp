#!/usr/bin/env bash
# setup.sh — Instalación de Expense Reconciler en una máquina nueva
#
# Uso: bash setup.sh
#
# Requiere: Docker Desktop instalado y corriendo.
# No requiere Python, Node ni Postgres en la máquina: todo va en contenedores.

set -e

echo "=== Expense Reconciler — Instalación ==="
echo ""

# 1. Verificar Docker
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker no está instalado."
    echo "Descárgalo de https://www.docker.com/products/docker-desktop/"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "ERROR: Docker no está corriendo. Abre Docker Desktop y vuelve a intentar."
    exit 1
fi

echo "✓ Docker disponible"

# 2. Generar .env si no existe
if [ ! -f .env ]; then
    echo "Generando .env con valores por defecto..."
    cp .env.example .env
    # Generar SECRET_KEY aleatoria
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${SECRET}/" .env
    rm -f .env.bak
    echo "✓ .env creado"
else
    echo "✓ .env ya existe"
fi

# 3. Levantar todo
echo ""
echo "Levantando servicios (esto puede tardar la primera vez: ~2 GB de imágenes)..."
docker compose up -d --wait --wait-timeout 180

echo ""
echo "=== Instalación completada ==="
echo ""
echo "  Frontend:  http://localhost:3000"
echo "  API:       http://localhost:8000"
echo "  Docs:      http://localhost:8000/docs"
echo ""
echo "  Usuario por defecto: sos@empresa.mx"
echo "  Contraseña:          SosTesting123"
echo ""
echo "  IMPORTANTE: cambia la contraseña en el primer login."
echo ""