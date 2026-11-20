#!/usr/bin/env bash
# Aplica las migraciones pendientes a una base que YA EXISTE. Corre DENTRO del
# contenedor del servicio `migraciones` (y también con `make migrar`, que lo
# invoca con `docker compose run`).
#
# POR QUE ESTE SCRIPT Y NO `docker compose exec` DESDE EL HOST
# ===========================================================
# `scripts/migrar.sh` armaba la consulta con `docker compose exec -T ... psql`.
# Eso funciona en el host, pero NO puede correr como servicio de Compose: dentro
# de un contenedor no hay CLI de Docker ni socket. Separar la aplicacion (aqui,
# con `psql` directo) de la decision de cuando correrla (Compose o el wrapper
# del host) deja UNA sola implementacion del bucle. Dos copias del bucle divergen:
# una aplica la migracion nueva y la otra la deja pendiente, y la del arranque
# es justo la que nadie mira.
#
# POR QUE ES SEGURO REAPLICAR
# ===========================
# Todas las migraciones son aditivas e idempotentes: `ADD COLUMN IF NOT EXISTS`,
# `CREATE TABLE IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`,
# `ON CONFLICT DO NOTHING`. Reaplicar una que ya estaba en el DDL no cambia nada
# y deja el registro al dia. Es lo que pasa en una base vieja sin
# `schema_migrations`: se crea el registro y se corren todas.
#
# EL REGISTRO SE ESCRIBE DESPUES
# ==============================
# Si el `psql` falla, `set -e` termina el script con 1 ANTES del INSERT. El
# servicio `migraciones` sale con 1, `expense-api` no arranca
# (`service_completed_successfully`) y `make up` lo reporta. Registrar antes
# seria marcar como al dia una base a la que no le corrio el DDL.

set -euo pipefail

HOST="${POSTGRES_HOST:-postgres-reconciler}"
USUARIO="${POSTGRES_USER:-admin}"
DB="${POSTGRES_DB:-expense_db}"
DIR="${MIGRACIONES_DIR:-/docker-entrypoint-initdb.d/migrations}"

# `pg_isready` no autentica, pero aqui solo se usa como "ya acepta conexiones".
# El healthcheck de Postgres ya garantiza esto cuando Compose inicia el
# servicio; la espera existe para que el script tambien se pueda correr a mano
# con `docker compose run --no-deps migraciones`.
intentos=0
until pg_isready -h "$HOST" -U "$USUARIO" -d "$DB" -q >/dev/null 2>&1; do
  intentos=$((intentos + 1))
  if [ "$intentos" -ge 30 ]; then
    echo "La base $HOST/$DB no respondio tras 30 intentos." >&2
    exit 1
  fi
  sleep 1
done

sql() {
  psql -X -q -v ON_ERROR_STOP=1 -h "$HOST" -U "$USUARIO" -d "$DB" -At -c "$1"
}

# Base anterior al registro: no se puede saber que le falta, pero las
# migraciones son idempotentes, asi que crear el registro y correrlas todas es
# honesto y no destructivo. Lo que NO se hace es adivinar el estado del esquema.
sql "CREATE TABLE IF NOT EXISTS schema_migrations (
       nombre VARCHAR(255) PRIMARY KEY,
       aplicada_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP)" >/dev/null

aplicadas=0
for archivo in "$DIR"/*.sql; do
  [ -e "$archivo" ] || continue
  nombre="$(basename "$archivo")"

  # El nombre se pega al SQL entre comillas simples, asi que se prohiben los
  # caracteres que podrian cerrar la cadena o meter otra sentencia. NO se
  # prohiben los acentos: el archivo real se llama
  # `0019_extraccion_multipaís_y_controles.sql` y una validacion ASCII lo
  # rechazo en la primera corrida, dejando la API sin arrancar. Medido el
  # 2026-11-20.
  case "$nombre" in
    *"'"*|*'"'*|*';'*|*'`'*|*' '*|*'\'*|*$'\n'*|*$'\r'*|*$'\t'*)
      echo "nombre de migracion no permitido: $nombre" >&2; exit 1 ;;
  esac
  case "$nombre" in
    *.sql) ;;
    *) echo "nombre de migracion no permitido: $nombre" >&2; exit 1 ;;
  esac

  ya="$(sql "SELECT 1 FROM schema_migrations WHERE nombre = '$nombre'")"
  if [ "$ya" = "1" ]; then
    continue
  fi

  echo "  aplicando $nombre..."
  psql -X -q -v ON_ERROR_STOP=1 -h "$HOST" -U "$USUARIO" -d "$DB" -f "$archivo"
  sql "INSERT INTO schema_migrations (nombre) VALUES ('$nombre')
       ON CONFLICT (nombre) DO NOTHING" >/dev/null
  aplicadas=$((aplicadas + 1))
done

if [ "$aplicadas" = 0 ]; then
  echo "  Base al dia: no habia migraciones pendientes."
else
  echo "  $aplicadas migracion(es) aplicada(s)."
fi
