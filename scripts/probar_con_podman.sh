#!/usr/bin/env bash
# Levanta el ambiente con PODMAN y deja todo listo para probar la digitalizacion.
#
# Uso:  bash scripts/probar_con_podman.sh
#
# NO usa Docker. Todo lo hace con `podman-compose`, que lee el mismo
# docker-compose.yml del proyecto.
#
# Lo que hace, en orden:
#   1. Revisa la memoria de la VM de Podman (y la sube si hace falta)
#   2. Levanta Postgres + la API
#   3. Espera a que la API responda
#   4. Crea el usuario de prueba, si no existe
#   5. Imprime el token, la empresa y los comandos de prueba
#
# Es idempotente: se puede volver a correr sin romper nada.

set -euo pipefail
cd "$(dirname "$0")/.."

API="http://127.0.0.1:8000"
EMAIL="prueba@despacho.mx"
PASSWORD="Prueba-2026-mx"

echo "==> 1. Memoria de la VM de Podman"
# El compose pide ~1.75 GB para la API y ~4 GB para Ollama. Con la VM en 2 GB no
# cabe y los contenedores mueren por OOM. Con 8 GB sobra.
MEM=$(podman machine inspect podman-machine-default --format '{{.Memory}}' 2>/dev/null || echo 0)
if [ "$MEM" -lt 6000000000 ] 2>/dev/null; then
  echo "    la VM tiene menos de 6 GB; subiendo a 8 GB"
  podman machine stop >/dev/null 2>&1 || true
  podman machine set --memory 8192 >/dev/null
  podman machine start >/dev/null
  sleep 8
else
  echo "    ya tiene memoria suficiente"
fi
podman machine list

echo
echo "==> 2. Levantando Postgres y la API (Ollama arranca como dependencia)"
# No se levanta `expense-front`: para probar la API no hace falta y consume RAM.
podman-compose up -d postgres-reconciler expense-api

echo
echo "==> 3. Esperando a que la API responda"
for i in $(seq 1 60); do
  if curl -s -m 2 "$API/health" >/dev/null 2>&1; then
    echo "    API arriba en ${i}s"
    break
  fi
  if [ "$i" = "60" ]; then
    echo "    LA API NO LEVANTO. Logs:"
    podman logs --tail 40 expense_api_service
    exit 1
  fi
  sleep 2
done
curl -s "$API/health"; echo

echo
echo "==> 4. Usuario de prueba"
# `scripts/crear_usuario.py` pide la contrasena por teclado (`getpass`), y eso no
# se puede automatizar. Se crea con la misma funcion que el script usa, para que
# el hash sea el mismo que haria el CLI.
if podman exec expense_api_service python -c "
import asyncio
from app.core.security import hashear_contrasena
from app.core.database import AsyncSessionLocal
from app.models.user import UserModel
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        existe = (await db.execute(
            select(UserModel).where(UserModel.email == '$EMAIL')
        )).scalar_one_or_none()
        if existe:
            print('YA_EXISTE')
            return
        db.add(UserModel(email='$EMAIL', nombre='Contador de Prueba',
                        password_hash=hashear_contrasena('$PASSWORD')))
        await db.commit()
        print('CREADO')

asyncio.run(main())
" 2>/dev/null | grep -q CREADO; then
  echo "    usuario creado"
else
  echo "    usuario ya existia"
fi

echo
echo "==> 5. Token y empresa"
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "    NO SE PUDO OBTENER EL TOKEN. Revisa el login."
  exit 1
fi
echo "    token: ${TOKEN:0:30}..."

COMPANY=$(curl -s -X POST "$API/api/v1/companies/" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Comercial del Norte SA de CV","tax_id":"CNO010101ABC"}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('id',''))
" 2>/dev/null || true)

if [ -z "$COMPANY" ]; then
  # Ya existe (el RFC es UNIQUE): se busca.
  COMPANY=$(podman exec expense_pgvector psql -U postgres -d expense_db -t -A \
    -c "SELECT id FROM companies WHERE tax_id='CNO010101ABC';" 2>/dev/null | tr -d ' ')
  echo "    la empresa ya existia"
else
  echo "    empresa creada"
fi

# Se guarda en /tmp para que los comandos de prueba sean cortos.
printf '%s' "$TOKEN"    > /tmp/er_token
printf '%s' "$COMPANY" > /tmp/er_cid

echo
echo "================================================================"
echo " LISTO"
echo "================================================================"
echo "  API (Swagger):  $API/docs"
echo "  usuario:        $EMAIL / $PASSWORD"
echo "  token en:       /tmp/er_token"
echo "  empresa en:     /tmp/er_cid"
echo
echo "--- PROBAR LA DIGITALIZACION (un archivo) ---"
echo "  curl -s -X POST $API/api/v1/tickets/extract-and-create \\"
echo "    -H \"Authorization: Bearer \$(cat /tmp/er_token)\" \\"
echo "    -F \"file=@~/Documents/Tickets/Tickets_app/IMG_4220.jpeg\" \\"
echo "    -F \"company_id=\$(cat /tmp/er_cid)\" | python3 -m json.tool"
echo
echo "--- ESCANEAR LA CARPETA ENTERA (simulacion, NO escribe) ---"
echo "  curl -s -X POST $API/api/v1/scan/ \\"
echo "    -H \"Authorization: Bearer \$(cat /tmp/er_token)\" \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d \"{\\\"company_id\\\": \\\"\$(cat /tmp/er_cid)\\\", \\\"simular\\\": true}\""
echo
echo "--- VER LA COLA DE REVISION ---"
echo "  curl -s \"$API/api/v1/tickets/review-queue?company_id=\$(cat /tmp/er_cid)\" \\"
echo "    -H \"Authorization: Bearer \$(cat /tmp/er_token)\" | python3 -m json.tool | head -40"
echo
echo "--- MIRAR LA BASE DE DATOS ---"
echo "  podman exec -it expense_pgvector psql -U postgres -d expense_db"
echo "  \\dt              (tablas)"
echo "  SELECT source_file, total_amount, extraction_status, confidence FROM tickets;"
echo
echo "--- MOTORES DE OCR DISPONIBLES ---"
echo "  curl -s $API/api/v1/scan/ocr -H \"Authorization: Bearer \$(cat /tmp/er_token)\" | python3 -m json.tool"
echo
echo "--- PARAR TODO ---"
echo "  podman-compose down       (conserva los datos)"
echo "  podman-compose down -v    (BORRA la base)"