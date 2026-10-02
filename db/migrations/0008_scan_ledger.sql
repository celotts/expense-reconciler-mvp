-- =====================================================================
-- 0008 - Escaneo de carpeta: el registro de los archivos
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- `db/init.sql` solo corre cuando Postgres arranca con el volumen vacio. Una
-- base que ya existe no ve los cambios del modelo, y este es el caso de todas
-- las instalaciones que ya estaban andando. Sin esta migracion, el escaner
-- levanta el API, responde 200 y revienta en la primera consulta con
-- UndefinedTable: "scan_files".
--
-- QUE RESUELVE
--
-- Dos tablas:
--
--   scan_files   el estado de cada archivo de la carpeta: que se vio, si se
--                pudo leer, cuantos intentos lleva y por que fallo el ultimo.
--
--   scan_events  la linea de tiempo. `attempts` dice cuantas veces se intento;
--                no dice en que orden ni que paso entre medio. Para auditar
--                hace falta el historico, y el log del contenedor no sirve
--                porque se pierde en cada reinicio.
--
-- LO QUE ESTA MIGRACION NO HACE, A PROPOSITO
--
-- No crea la carpeta de tickets ni la lee. `TICKETS_INPUT_DIR` se resuelve y se
-- valida en Python al arrancar (ver app/core/config.py), no aqui: una ruta del
-- sistema de archivos de la maquina que corre la app no es dato de la base, y
-- meterla en un .sql versionado la escribe en todas las instalaciones.
--
-- Tampoco indexa el contenido de los archivos. `content_hash` se calcula
-- leyendo los bytes en cada corrida, que es la unica forma de saber si el
-- archivo cambio de verdad. Un indice de contenido en la base habria que
-- mantenerlo con un trigger que lee el disco, y un trigger que lee el disco
-- desde la base no se puede probar.
--
-- SOBRE `relative_path` Y NO LA ABSOLUTA
--
-- Ver app/models/scan_file.py. Resumen: la ruta absoluta cambia cuando la
-- carpeta se mueve, y deja el home de alguien escrito 500 veces en una tabla
-- que se exporta.
--
-- ---------------------------------------------------------------------
-- Como aplicarla (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0008_scan_ledger.sql
--
-- Es idempotente: se puede correr las veces que haga falta.
-- ---------------------------------------------------------------------
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS scan_files (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Relativa a TICKETS_INPUT_DIR. La concatenacion con la carpeta se hace
    -- en un solo lugar del codigo (scan_service), no en cada consulta.
    relative_path       VARCHAR(500) NOT NULL,

    -- SHA-256 de los bytes. Decide si el archivo cambio, no `mtime`: un `touch`
    -- cambia la fecha sin cambiar el contenido, y reprocesar por eso es tirar
    -- OCR a un archivo que ya se leyo.
    content_hash        VARCHAR(64) NOT NULL,
    file_size           BIGINT,
    file_mtime          TIMESTAMP WITH TIME ZONE,

    -- Lo que dicen los BYTES, no el nombre. Ver app/core/archivo_real.py.
    detected_format     VARCHAR(20),

    -- La extension que traia el archivo. Se guarda aparte del formato real
    -- para poder explicar por que un `ticket.pdf` se leyo como imagen, y para
    -- notar un renombrado que no cambia el contenido.
    declared_extension  VARCHAR(20),

    status              VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    attempts            INTEGER NOT NULL DEFAULT 0,

    -- Que escalon lo leyo: ocr, llm, pdf_text, rules.
    read_by             VARCHAR(20),

    -- Por que no se pudo LEER el archivo. Distinto de
    -- `tickets.validation_errors`, que es la opinion del gate sobre el papel:
    -- aqui va "tesseract no esta instalado", que es un problema de la maquina.
    last_error          TEXT,

    -- SET NULL y no CASCADE: si alguien borra el ticket, el archivo sigue
    -- en el disco y el registro de que se leyo tiene que seguir ahi. Con
    -- CASCADE el proximo escaneo lo trataria como nuevo.
    ticket_id           UUID REFERENCES tickets(id) ON DELETE SET NULL,

    -- NULLABLE a proposito: el archivo esta en el disco antes de que nadie
    -- decida a que empresa pertenece, y un escaneo puede correr sin elegir
    -- empresa solo para inventariar.
    company_id          UUID REFERENCES companies(id) ON DELETE SET NULL,

    first_seen_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at     TIMESTAMP WITH TIME ZONE,
    processed_at        TIMESTAMP WITH TIME ZONE,

    -- El estado se valida tambien en Python, pero los tests corren contra
    -- SQLite, que no mira esto. El primer lugar donde se descubre un valor mal
    -- escrito seria un INSERT en produccion.
    CONSTRAINT ck_scan_files_status
        CHECK (status IN ('PENDIENTE', 'PROCESADO', 'DUPLICADO', 'ERROR', 'NO_SOPORTADO')),

    -- Un archivo, una fila. Sin esto, dos corridas simultaneas insertan dos
    -- filas para el mismo archivo y la idempotencia se pierde justo cuando
    -- hace falta: cuando hay dos personas escaneando.
    CONSTRAINT uq_scan_files_relative_path UNIQUE (relative_path)
);

CREATE TABLE IF NOT EXISTS scan_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- CASCADE aqui si, a diferencia de los otros dos: un evento sin su archivo
    -- no significa nada. Si se borra el archivo, sus eventos tambien.
    scan_file_id  UUID NOT NULL REFERENCES scan_files(id) ON DELETE CASCADE,

    action        VARCHAR(20) NOT NULL,
    detail        TEXT,

    -- Quien lo disparo: el correo del token, o 'sistema' para el automatico.
    -- Texto y no llave foranea por la misma razon que
    -- `tickets.spot_checked_by`: una baja es `is_active = false`, y el
    -- veredicto tiene que sobrevivir a que alguien borre la fila.
    actor         VARCHAR(255),

    -- Se copia la confianza del intento para que el historico no dependa de
    -- leer el ticket, que puede haber cambiado desde entonces.
    confidence    NUMERIC(4, 3),

    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_scan_events_action
        CHECK (action IN ('VISTO', 'CREADO', 'ACTUALIZADO', 'SIN_CAMBIOS',
                          'OMITIDO', 'ERROR', 'REINTENTO', 'BORRADO'))
);

-- La cola de trabajo del escaner: lo que quedo pendiente o fallo.
CREATE INDEX IF NOT EXISTS ix_scan_files_status
    ON scan_files (status);

-- Para encontrar "estos bytes ya los leimos en otro archivo". NO es UNIQUE a
-- proposito: los bytes iguales en dos rutas son dos archivos que el operador
-- puede querer tener los dos registrados, y la deduplicacion la decide el
-- escaner, no una constraint.
CREATE INDEX IF NOT EXISTS ix_scan_files_content_hash
    ON scan_files (content_hash);

CREATE INDEX IF NOT EXISTS ix_scan_files_company
    ON scan_files (company_id, status);

-- El historico se lee por archivo y en orden cronologico. Sin esto, "que le
-- paso a este archivo" es un seq scan de todos los eventos de todos los
-- archivos.
CREATE INDEX IF NOT EXISTS ix_scan_events_file_created
    ON scan_events (scan_file_id, created_at);

COMMENT ON TABLE scan_files IS
    'Un archivo de TICKETS_INPUT_DIR y que se ha hecho con el. relative_path es relativa a la carpeta, no absoluta.';

COMMENT ON COLUMN scan_files.relative_path IS
    'Ruta relativa a TICKETS_INPUT_DIR. No se guarda la absoluta: cambia al mover la carpeta y escribe el home de alguien en una tabla que se exporta.';

COMMENT ON COLUMN scan_files.content_hash IS
    'SHA-256 de los bytes. Es lo que decide si el archivo cambio desde la ultima vez.';

COMMENT ON COLUMN scan_files.attempts IS
    'Intentos de lectura. Separa "fallo una vez" de "lleva quince corridas fallando".';

COMMENT ON COLUMN scan_files.last_error IS
    'Por que no se pudo LEER el archivo. No es lo mismo que tickets.validation_errors, que es la opinion del gate sobre el contenido.';

COMMENT ON TABLE scan_events IS
    'Linea de tiempo de los cambios de estado de un archivo. attempts no la sustituye: dice cuantas veces, no en que orden.';

COMMIT;

-- ---------------------------------------------------------------------
-- Verificacion
-- ---------------------------------------------------------------------
SELECT 'scan_files' AS tabla, count(*) AS filas FROM scan_files
UNION ALL
SELECT 'scan_events', count(*) FROM scan_events;
