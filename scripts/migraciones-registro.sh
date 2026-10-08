#!/usr/bin/env bash
# Registra una migracion como aplicada en `schema_migrations`.
#
# POR QUE ESTE SCRIPT Y NO UNA LINEA EN EL OTRO
# ----------------------------------------------
# `migrar.sh` lo invoca DESPUES de que `psql` salio con 0. Si el registro viviera
# en el mismo script, bastaria un `INSERT` de mas y una base queda marcada como
# al dia sin haber corrido el DDL. Separarlo hace que el unico modo de registrar
# sea "el archivo se aplico".
#
# `INSERT ... ON CONFLICT DO NOTHING` porque `init.sql` se puede volver a correr
# (es idempotente a proposito) y estas filas ya pueden estar.
#
# `aplicada_at` es el momento real de aplicacion, no el de escribir esta fila.
# No lleva usuario: el registro dice QUE se aplico, no QUIEN lo autorizo — el
# DDL no tiene firma, y un campo de autor lleno de "docker" seria teatro.
#
# POR QUE EL NOMBRE SE PEGA AL SQL Y NO SE VALORA UNA VARIABLE
# ----------------------------------------------------------
# Lo natural seria `-v nombre="$NOMBRE" -c "... VALUES (:'nombre')"`, y **no
# funciona**: en esta version de `psql` la interpolacion `:'var'` no se aplica y
# la sentencia llega a Postgres con los dos puntos, que es un error de sintaxis.
# Se probo por `-c` y por `-f -` y en los dos casos fallo.
#
# Pegar el nombre al SQL es aceptable **porque el nombre se valida antes**:
# `VALIDACION` solo deja pasar algo que parece un archivo mio, sin comillas ni
# punto y coma. Sin esa validacion, un archivo llamado `x'; DROP TABLE tickets; --.sql`
# seria DDL. El `ls` no es una frontera de confianza: el directorio tambien se
# puede escribir, y por eso la comprobacion existe en vez de confiar.

set -euo pipefail
cd "$(dirname "$0")/.."

NOMBRE="${1:-}"
if [ -z "$NOMBRE" ]; then
  echo "uso: bash scripts/migraciones-registro.sh <archivo.sql>" >&2
  exit 1
fi

# Digitos, letras, punto y guion bajo, y `.sql` al final. Sin espacios, sin
# comillas, sin `;`. Si un dia un archivo se llama con espacios, esto hay que
# ampliarlo — y el fallo se ve aqui, no en una sentencia de SQL.
VALIDACION='^[A-Za-z0-9_][A-Za-z0-9_.-]*\.sql$'
if ! [[ "$NOMBRE" =~ $VALIDACION ]]; then
  echo "nombre de migracion con caracteres no permitidos: $NOMBRE" >&2
  echo "se permiten digitos, letras, punto y guion bajo, terminados en .sql" >&2
  exit 1
fi

docker compose exec -T postgres-reconciler psql \
  -U postgres -d expense_db -q \
  -v ON_ERROR_STOP=1 \
  -c "CREATE TABLE IF NOT EXISTS schema_migrations (
        nombre VARCHAR(255) PRIMARY KEY,
        aplicada_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP)" \
  -c "INSERT INTO schema_migrations (nombre) VALUES ('$NOMBRE')
      ON CONFLICT (nombre) DO NOTHING"
