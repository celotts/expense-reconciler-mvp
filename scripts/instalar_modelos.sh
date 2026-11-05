#!/bin/sh
# Descarga los modelos de IA declarados en la `.env`, si no estan.
#
# POR QUE EXISTE
# =============
#
# MEDIDO el 2026-10-15. La `.env` declaraba `OLLAMA_VISION_MODEL=qwen2.5vl:3b` y el
# contenedor de Ollama **no tenia ningun modelo**: `ollama list` salia vacio y el
# volumen ocupaba 32 KB. El sistema escaneaba solo con Tesseract y su techo de
# exactitud era el del OCR, sin que nada lo dijera.
#
# Peor: `/api/v1/scan/ia` decia `faltan: []` y `disponible: true` con cero modelos
# instalados, porque para Ollama el calculo de `faltan` estaba cortocircuitado con
# un `pass`. El endpoint que existe para decir *que modelo falta* no lo decia, y
# el cartel del front —que solo aparece si hay algo que arreglar— no aparecia.
# O sea que lo unico que se veia era "el sistema no lee bien los montos".
#
# Instalar a mano en cada maquina es un paso que se olvida y que nadie nota que
# falta. Este script lo hace parte de `make up`.
#
# POR QUE NO FALLA EL ARRANQUE SI LA DESCARGA FALLA
# ===================================================
#
# Porque la app SIN modelo tambien funciona: escanea con reglas y con OCR, mas
# lento y menos exacto, pero funciona. Un `pull` que falla por red no puede dejar
# sin sistema a alguien que solo quiere escanear sus comprobantes. Por eso el
# script **avisa y sale con 0**; y quien lee el aviso en la pantalla es
# `/scan/ia`, que ya dice exactamente cual falta y con que comando.
#
# QUE ES IDEMPOTENTE
# =================
#
# Pregunta antes de descargar. En una maquina con los modelos puestos, esto no
# baja nada y termina en un par de segundos: `make up` no tiene que pagar 3.2 GB
# cada vez.

set -e

BASE="${OLLAMA_HOST:-http://ollama:11434}"
export OLLAMA_HOST="$BASE"

echo "Modelos de IA: esperando a Ollama en $BASE"
i=0
while [ "$i" -lt 60 ]; do
  if ollama list >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if ! ollama list >/dev/null 2>&1; then
  echo "AVISO: Ollama no respondio en $BASE. Los comprobantes se leeran con OCR."
  echo "       Para instalar el modelo a mano:  docker compose exec ollama ollama pull <modelo>"
  exit 0
fi

instalados="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')"

# Un nombre sin etiqueta compara contra `nombre:latest`, que es como los lista
# `ollama list`. Comparar el texto entero haria que `qwen2.5:3b` pareciera
# ausente con `qwen2.5:3b` instalado, que es el mismo bug de emparejar
# etiquetas que se corrigio en la medicion de exactitud.
#
# MEDIDO el 2026-10-16: esto se escribio dos veces mal y las dos veces el
# sintoma fue el mismo -"vuelve a bajar 3.2 GB en cada `make up`"-, que es un
# sintoma que parece del `ollama pull` y era de la comprobacion de antes.
#
# 1. La primera version comparaba con `[ -z "$x" ] && continue` dentro de un
#    `while` en una tuberia, con `set -e`. Cada `&&` que evalua a falso devuelve
#    1, `set -e` lo lee como fallo y sale del subshell en la PRIMERA linea, antes
#    de mirar la comparacion.
# 2. La segunda estaba al reves: devolvia 1 cuando el modelo SI estaba instalado,
#    y el `if` leia ese 1 como "falta". Descargaba siempre.
#
# Por eso: `for` en vez de tuberia (nada de subshell), `if` en vez de `&&` (nada
# que pueda devolver 1), y el nombre dice lo que el 0 significa. En shell, 0 es
# "si" y cualquier otra cosa es "no"; un nombre de funcion que hay que traducir
# para saber cual de las dos es es un nombre mal puesto.
esta_instalado() {
  base="${1%%:*}"
  for nombre in $instalados; do
    if [ "${nombre%%:*}" = "$base" ]; then
      return 0
    fi
  done
  return 1
}

# Los modelos se leen de las MISMAS variables que usa la app, y llegan por
# `env_file` —el mismo mecanismo y en el mismo orden— y no por interpolacion de
# Compose, que resuelve primero del shell. Ver el comentario en
# `docker-compose.yml`: con `${OLLAMA_MODEL}` este script descargo `moondream`
# mientras la app usaba `qwen2.5:3b`.
for modelo in "${OLLAMA_MODEL:-}" "${OLLAMA_VISION_MODEL:-}"; do
  if [ -z "$modelo" ]; then
    continue
  fi
  if esta_instalado "$modelo"; then
    echo "  $modelo ya esta"
  else
    echo "  descargando $modelo (puede tardar varios minutos la primera vez)..."
    if ollama pull "$modelo"; then
      echo "  $modelo instalado"
    else
      echo "AVISO: no se pudo descargar $modelo."
      echo "       El escaneo seguira funcionando con OCR, pero con menos exactitud."
      echo "       Reintenta con:  docker compose exec ollama ollama pull $modelo"
    fi
  fi
done

echo "Modelos listos:"
ollama list