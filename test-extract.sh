#!/bin/bash
# Prueba de extraccion de ticket por imagen
set -euo pipefail

FILE="/Users/carloslott/Downloads/factura_prueba.jpeg"

if [ ! -f "$FILE" ]; then
  echo "ERROR: No existe el archivo: $FILE"
  exit 1
fi

echo "Enviando: $FILE"
curl -sS --max-time 300 -X POST "http://localhost:8000/api/v1/tickets/extract" \
  -F "file=@$FILE;type=image/jpeg" \
  -F "file_type=image" \
  -w "\nHTTP_STATUS:%{http_code}\n"