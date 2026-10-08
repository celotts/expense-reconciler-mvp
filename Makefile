.PHONY: build up up-force down logs ps stats disk clean insomnia insomnia-check \
        migrar migrar-list migrar-estado

build:
	docker compose build

# up reconstruye las imagenes (usa cache de capas) antes de levantar,
# para no usar imagenes vieja cuando cambia requirements.txt o el codigo del front.
up: build
	docker compose up -d --remove-orphans

# up-force ignora la cache de Docker y rehace todas las capas.
up-force:
	docker compose build --no-cache
	docker compose up -d --remove-orphans

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f

# Consumo de RAM/CPU en vivo contra la cuota de cada contenedor.
stats:
	docker stats --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'

# Espacio que ocupa Docker en disco.
disk:
	docker system df

# Regenera la coleccion de Insomnia desde el OpenAPI de la API viva. El
# archivo se versiona, asi que sin esto se queda viejo solo: la coleccion
# anterior estaba exportada de 2025-01-21 y le faltan 27 de las 52 rutas.
insomnia:
	python3 scripts/generar_insomnia.py

# Falla si el archivo no corresponde a la API actual. Para CI, o para cuando
# anades una ruta y te quieres acordar de que hay que regenerar.
insomnia-check:
	python3 scripts/generar_insomnia.py --check

# ===========================================================================
# Migraciones: SOLO para una base que YA EXISTE
# ===========================================================================
#
#   Instalacion nueva  ->  `make up`.       Las migraciones NO se necesitan.
#   Base ya existente   ->  `make migrar`.  Esta es la unica razon de existir.
#
# POR QUE LA LOGICA ESTA EN scripts/migrar.sh Y NO AQUI
# -----------------------------------------------------
# La primera version eran dos bloques de recipe y fallaba de una forma que es
# facil de no ver: imprimia el estado, no aplicaba nada y salia con codigo 0.
# Un `make` que dice "ok" y no hace nada es peor que uno que falla, porque el
# operador cree que migro. Las dos causas son de Make, no bugs:
#
#   1. Cada linea de un recipe es un shell DISTINTO. Un `if ... fi` y un
#      `for ... done` en lineas separadas son dos procesos que no comparten ni
#      las variables, y el `if` se comia el bucle entero.
#   2. El shell por defecto es `/bin/sh`, no bash: `read -p`, `case` con `;;`
#      y los acentos en los patrones se portan mal.
#
# En un script hay un shell, el quoting es normal y el exit code es el del
# script. Ademas se corre sin `make`, que es lo que necesita alguien que no
# conoce el Makefile.
#
# POR QUE HACE FALTA LA TABLA `schema_migrations`
# ----------------------------------------------
# Antes no se podia saber si una base necesitaba migraciones. Se probo mirar el
# esquema y NO sirve: `init.sql` replica las 12 migraciones, asi que una base
# creada desde cero y una migrada son IDENTICAS. Se intento usar
# `cierres_periodo` como discriminante (es la 0007) y dio "esta base es vieja"
# sobre una base recien creada, porque `init.sql` tambien la crea.
#
# Un esquema completo no dice de que parte del camino viene la base. Por eso
# existe el registro: es lo mismo que hace Alembic con su tabla de version, que
# es justo la razon por la que este proyecto no necesita Alembic todavia.
#
# POR QUE NO HAY ALEMBIC
# -----------------------
# El README lo lista como proximo paso y no esta. Con 12 migraciones escritas a
# mano y una base que se crea desde cero, un bucle sobre los archivos en orden
# hace lo mismo sin una dependencia mas. Alembic tiene sentido cuando las
# migraciones se GENERAN de los modelos; aqui son el unico sitio donde el DDL
# esta escrito, y autogenerarlas seria justo lo que este repo no quiere.
#
# LA RUTA DENTRO DEL CONTENEDOR es `/docker-entrypoint-initdb.d/migrations/`,
# que es donde el compose monta `./db`. NO es `/db/`: el unico montaje es el del
# init, y usar la ruta equivocada da "No such file or directory" y parece que
# las migraciones no existen.

# Que migraciones faltan en esta base. NO aplica nada.
#
# Este es el comando de la duda "¿esto lo necesito?": responde mirando el
# registro, no suponiendo, y separa tres casos que antes se confundian — base al
# dia, base con pendientes, y base SIN registro (creada antes de que la tabla
# existiera), donde no se puede saber y hay que decirlo en vez de adivinar.
migrar-estado:
	@bash scripts/migrar.sh --estado

# Aplica las que faltan, en orden, dentro del contenedor.
#
#   make migrar              -> pregunta
#   make migrar MIGRAR_SI=1  -> no pregunta (script y CI)
#
# `IF NOT EXISTS` y `DROP CONSTRAINT IF EXISTS` hacen que reaplicar una
# migracion no rompa nada. No hay transaccion global: cada archivo va entero o
# no va, y por eso el registro se escribe SOLO despues de que `psql` salio con
# 0. Registrar antes seria marcar como al dia una base a la que no le corrio
# el DDL.
migrar:
	@bash scripts/migrar.sh $(if $(MIGRAR_SI),--si,)

# Borra contenedores, red y volumenes (INCLUYE la base de datos y los modelos).
clean:
	docker compose down -v

# Borra solo imagenes huerfanas y cache de build. Conserva volumenes y datos.
# No toca imagenes de infraestructura de Docker Desktop (kindest/node, envoy).
prune:
	docker image prune -f
	docker builder prune -f
