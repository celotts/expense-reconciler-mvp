#!/usr/bin/env bash
# Muestra o aplica las migraciones pendientes de una base que YA EXISTE.
#
# POR QUE ESTE SCRIPT YA NO APLICA NADA POR SU CUENTA
# ===================================================
# Aqui vivia el bucle que corria los .sql con `docker compose exec`. Funcionaba
# si lo llamaba una persona desde el host, pero NO podia correr como servicio
# de Compose: dentro de un contenedor no hay CLI de Docker ni socket. Y `make
# up` tiene que aplicar las migraciones sin que nadie recuerde llamar a nada.
# Las dos opciones eran dos copias del bucle o una sola; dos copias divergen, y
# la del arranque es justo la que nadie mira. La logica vive en
# `scripts/migrar_auto.sh`, que corre dentro del servicio `migraciones`. Este
# archivo es el adaptador del host (y el que responde `--estado`).
#
# POR QUE HACE FALTA LA TABLA `schema_migrations`
# ----------------------------------------------
# Mirar el esquema NO sirve para saber si una base necesita migraciones:
# `init.sql` replica todas las migraciones, asi que una base creada de cero y
# una migrada son IDENTICAS. Se intento usar `cierres_periodo` como
# discriminante y dio "base vieja" sobre una base recien creada. El registro es
# lo mismo que hace Alembic con su tabla de version, sin la dependencia.
#
# USO
# ---
#   bash scripts/migrar.sh            # pregunta
#   bash scripts/migrar.sh --si       # no pregunta, para script y CI
#   bash scripts/migrar.sh --estado   # solo dice que falta, no aplica

set -euo pipefail
cd "$(dirname "$0")/.."

SERVICIO="postgres-reconciler"
# El usuario hardcodeado era `postgres`, un rol que NO existe: el entrypoint
# crea solo `POSTGRES_USER` (admin). MEDIDO el 2026-11-20: con ese literal este
# script decia "La base no respondio" aunque la base estuviera sana, porque el
# `psql` moria con `FATAL: role "postgres" does not exist` y el `2>/dev/null` se
# lo tragaba. Se lee del mismo `.env` que configura Compose, con el literal
# `admin` solo como ultimo recurso si alguien corrio el script sin .env.
USUARIO="$(sed -n 's/^POSTGRES_USER=//p' .env 2>/dev/null | head -1)"
USUARIO="${USUARIO:-admin}"
DB="$(sed -n 's/^POSTGRES_DB=//p' .env 2>/dev/null | head -1)"
DB="${DB:-expense_db}"

CONFIRMAR=1
case "${1:-}" in
  --si)     CONFIRMAR=0 ;;
  --estado) CONFIRMAR=-1 ;;
  *)        CONFIRMAR=1 ;;
esac

sql() { docker compose exec -T "$SERVICIO" psql -U "$USUARIO" -d "$DB" -At -c "$1" < /dev/null 2>/dev/null; }

esta_levantada() {
  [ -n "$(sql "SELECT 1")" ]
}

if ! esta_levantada; then
  echo "La base no respondio. Levanta el ambiente con 'make up' y reintenta." >&2
  exit 1
fi

pendientes() {
  local f n ya
  for f in db/migrations/*.sql; do
    n=$(basename "$f")
    ya=$(sql "SELECT 1 FROM schema_migrations WHERE nombre = '$n'")
    [ "$ya" = "1" ] || echo "$n"
  done
}

if [ "$(sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" != "t" ]; then
  # El servicio de arranque crea el registro y corre las migraciones (todas son
  # idempotentes), asi que esto ya no bloquea `make up`. Aqui se dice porque el
  # modo --estado no puede contestar sin registro: adivinar seria peor.
  echo "Esta base es anterior al registro de migraciones." >&2
  echo 'make up lo crea y aplica las pendientes. make migrar tambien.' >&2
  exit 1
fi

faltan=$(pendientes)

if [ "$CONFIRMAR" = "-1" ]; then
  echo "Migraciones en db/migrations/:"
  ls -1 db/migrations/ | sed 's/^/  /'
  echo ""
  if [ -z "$faltan" ]; then
    echo "  Esta base esta AL DIA. No hace falta migrar nada."
  else
    echo "  FALTAN en esta base:"
    echo "$faltan" | sed 's/^/    /'
    echo "  Para aplicarlas:  make migrar"
  fi
  exit 0
fi

if [ -z "$faltan" ]; then
  echo "  Esta base esta AL DIA. No hace falta migrar nada."
  exit 0
fi

echo "  FALTAN en esta base:"
echo "$faltan" | sed 's/^/    /'
echo ""

if [ "$CONFIRMAR" = "1" ]; then
  echo "Esto es para una base que YA EXISTE."
  echo "Si acabas de instalar de cero, 'init.sql' ya hizo todo y esto no hace falta."
  echo ""
  read -r -p "¿Aplicar las que faltan? [s/N] " r
  case "$r" in
    s|S|si|SI) ;;
    *) echo "Cancelado."; exit 1 ;;
  esac
fi

# El MISMO servicio que corre en el arranque. `--no-deps` para no volver a
# levantar Postgres ni arrastrar a la API: solo se quiere aplicar el DDL.
docker compose run --rm --no-deps migraciones
