.PHONY: build up up-force down logs ps stats disk clean insomnia insomnia-check

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

# Borra contenedores, red y volumenes (INCLUYE la base de datos y los modelos).
clean:
	docker compose down -v

# Borra solo imagenes huerfanas y cache de build. Conserva volumenes y datos.
# No toca imagenes de infraestructura de Docker Desktop (kindest/node, envoy).
prune:
	docker image prune -f
	docker builder prune -f
