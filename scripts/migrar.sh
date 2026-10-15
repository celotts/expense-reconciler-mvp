#!/usr/bin/env bash
# Aplica las migraciones que faltan en una base que YA EXISTE.
#
# POR QUE ESTE SCRIPT Y NO UN RECIPE DE MAKE
# ==========================================
#
# La primera version de esto eran dos bloques del `Makefile`, y fallaba de una
# forma que es facil de no ver: imprimia el estado de la base, no aplicaba nada,
# y salia con codigo 0. Un `make` que dice "ok" y no hace nada es peor que uno
# que falla, porque el operador cree que migro.
#
# La causa son dos cosas de Make, ambas conocidas y ninguna un bug:
#
#   1. **Cada linea de un recipe es un shell distinto.** Un `if ... fi` y un
#      `for ... done` en lineas separadas son dos procesos que no comparten ni
#      las variables.
#   2. **El shell por defecto es `/bin/sh`, no bash**, y `read -p`, `case` con
#      `;;` y los acentos en los patrones se portan mal.
#
# En un script hay un shell, el quoting es normal, y el exit code es el del
# script. Ademas el script se puede correr solo, sin `make`, que es lo que
# necesita alguien que no conoce el Makefile.
#
# USO
# ---
#   bash scripts/migrar.sh            # pregunta
#   bash scripts/migrar.sh --si       # no pregunta, para script y CI
#   bash scripts/migrar.sh --estado   # solo dice que falta, no aplica

set -euo pipefail
cd "$(dirname "$0")/.."

SERVICIO="postgres-reconciler"
DB="expense_db"
USUARIO="postgres"
# El compose monta `./db` en `/docker-entrypoint-initdb.d` (docker-compose.yml:25).
# NO es `/db`. Usar la ruta equivocada da "No such file or directory" y parece
# que las migraciones no existen.
CONTENEDOR_DB="/docker-entrypoint-initdb.d/migrations"

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

# Los archivos de migracion que existen en el HOST, en orden.
pendientes() {
  local f n ya
  for f in db/migrations/*.sql; do
    n=$(basename "$f")
    ya=$(sql "SELECT 1 FROM schema_migrations WHERE nombre = '$n'")
    [ "$ya" = "1" ] || echo "$n"
  done
}

if ! esta_levantada; then
  echo "La base no respondio. Levanta el ambiente con 'make up' y reintenta." >&2
  exit 1
fi

echo "Migraciones en db/migrations/:"
ls -1 db/migrations/ | sed 's/^/  /'
echo ""

# Una base creada antes de que existiera `schema_migrations` no tiene registro, y
# SIN registro no se puede saber que le falta. Adivinar seria peor que no
# decir nada: se corre el archivo entero a ciegas sobre una base que quizas ya
# esta al dia.
if [ "$(sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" != "t" ]; then
  echo "ATENCION: esta base es anterior al registro de migraciones." >&2
  echo "No se puede saber que le falta, y 'init.sql' no corre en una base que ya" >&2
  echo "existe. Las dos salidas honestas:" >&2
  echo "  1. compara db/init.sql con lo que hay, y corrige a mano lo que falte, o" >&2
  echo "  2. crea la base de cero:  docker compose down -v && make up" >&2
  echo "NO corras esto a ciegas." >&2
  exit 1
fi

faltan=$(pendientes)

if [ -z "$faltan" ]; then
  echo "  Esta base esta AL DIA. No hace falta migrar nada."
  exit 0
fi

echo "  FALTAN en esta base:"
echo "$faltan" | sed 's/^/    /'
echo ""

if [ "$CONFIRMAR" = "-1" ]; then
  echo "  Para aplicarlas:  make migrar"
  exit 0
fi

if [ "$CONFIRMAR" = "1" ]; then
  echo "Esto es para una base YA EXISTE."
  echo "Si acabas de instalar de cero, 'init.sql' ya hizo todo y esto no hace falta."
  echo ""
  read -r -p "¿Aplicar las que faltan? [s/N] " r
  case "$r" in
    s|S|si|SI) ;;
    *) echo "Cancelado."; exit 1 ;;
  esac
fi

echo ""
aplicadas=0
# `docker compose exec -T` NO hereda un stdin que valga: se queda con el
# descriptor 0 del bucle y se lo come. Medido: con dos migraciones pendientes
# aplicaba la primera, registraba la primera, y el `read` del bucle ya no tenia
# que leer — Loop sobre la lista de pendientes, aplicaba 1 de 2 y salia
# diciendo "1 migracion(es) aplicada(s)". Sin error, sin fallo, y la segunda
# migracion sin aplicar.
#
# Por eso el bucle lee de un descriptor propio (fd 3) y los `docker` reciben
# `/dev/null` explicito: cada uno es un proceso que no debe ver la entrada del
# bucle ni la del teclado.
while read -r n <&3; do
  [ -n "$n" ] || continue
  echo "  aplicando $n..."
  if ! docker compose exec -T "$SERVICIO" psql -U "$USUARIO" -d "$DB" \
        -q -v ON_ERROR_STOP=1 -f "$CONTENEDOR_DB/$n" < /dev/null; then
    echo ""
    echo "  FALLO en $n. La base quedo a medio camino." >&2
    echo "  Corrigela a mano y vuelve a correr 'make migrar'." >&2
    echo "  (migraciones-registro.sh revisa que conste como aplicada)" >&2
    exit 1
  fi
  # Se registra SOLO si el archivo se aplico entero. Registrar antes seria
  # mentirle al proximo que la base esta al dia cuando no lo esta.
  bash "$(dirname "$0")/migraciones-registro.sh" "$n" < /dev/null
  aplicadas=$((aplicadas + 1))
done 3<<< "$faltan"

echo ""
if [ "$aplicadas" = "0" ]; then
  echo "  No habia nada pendiente."
else
  echo "  $aplicadas migracion(es) aplicada(s)."
fi
echo ""