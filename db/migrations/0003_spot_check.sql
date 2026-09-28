-- =====================================================================
-- 0003 - Muestreo de exactitud: verificar el 96% con evidencia, no con fe
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Misma razon que 0002: `db/init.sql` solo corre con el volumen vacio, asi
-- que agregar columnas al modelo no crea nada en una base que ya existe. Sin
-- esta migracion, la app levanta y revienta en la primera consulta con
-- UndefinedColumnError.
--
-- QUE RESUELVE
--
-- El objetivo de exactitud (>= 96% clasificado bien sin intervencion humana)
-- era una cifra sin forma de verificarla. Nadie podia answering: "de donde
-- sale el 96%". Con esta migracion hay una muestra que una persona revisa
-- contra el papel, y la respuesta sale de ahi.
--
-- LO QUE ESTA MIGRACION NO HACE, A PROPOSITO
--
-- No marca ningun ticket preexistente para revision. Esos tickets nunca
-- pasaron por una decision de muestreo, y marcarlos ahora seria inventar una
-- revision que nadie hizo. Mismo criterio que 0002 con `confidence`: lo que no
-- se sabe, se queda en NULL, y el reporte lo dice en vez de rellenarlo.
--
-- Se pueden sembrar despues a mano, si de verdad se quiere medir el historico:
--
--   UPDATE tickets SET spot_check_status = 'PENDIENTE'
--    WHERE extraction_status = 'AUTO_APROBADO'
--      AND source_hash IS NOT NULL
--      AND spot_check_status IS NULL
--      AND (('x' || substr(source_hash, 1, 8))::bit(32)::int % 20) = 0;
--
-- COMO APLICARLA (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0003_spot_check.sql
--
-- Es idempotente: se puede correr las veces que haga falta.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Las columnas nuevas
-- ---------------------------------------------------------------------
--
-- spot_check_status: NULL = fuera de la muestra. Es el caso de la gran
-- mayoria de los tickets, y por eso es NULL y no un valor tipo 'NO'. Un valor
-- inventado para el 95% de las filas hace que el indice de la cola crezca con
-- todo el historico y que "fuera de la muestra" sea indistinguible de
-- "elegido y todavia no revisado".
--
-- spot_check_wrong_fields: que campos estaban mal, no solo que estaban mal.
-- "96% correcto" no dice que arreglar. "El 3% que falla es casi todo la
-- fecha" si. Sin esto el muestreo produce un numero y ninguna accion.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS spot_check_status VARCHAR(20);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS spot_checked_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS spot_check_notes TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS spot_check_wrong_fields TEXT;

-- ---------------------------------------------------------------------
-- 1b. El subtotal, que se usaba y no se guardaba
-- ---------------------------------------------------------------------
--
-- Este no es un campo mas del muestreo: es un agujero que el propio muestreo
-- revelo al intentar medirlo. `subtotal` se extraia, se le pasaba al gate para
-- validar `subtotal + IVA == total`, y la confianza dependia de que estuviera
-- presente. Despues se descartaba: no habia columna.
--
-- Las tres consecuencias son concretas:
--
-- - La decision del gate no era auditable. El ticket se aprobo porque el
--   subtotal cuadraba, y no habia forma de volver a comprobar esa cuadra.
-- - El muestreo preguntaba si el subtotal estaba bien leido, sobre un campo
--   que el sistema no conservaba. El revisor tenia que buscarlo a mano en el
--   `raw_text`, y en la ruta de vision ese `raw_text` es lo que el modelo
--   devolvio, que no siempre trae el subtotal. La pregunta no tenia con que
--   responderse.
-- - La conciliacion no podia repetir la cuenta.
--
-- Va en esta migracion y no en otra porque es la que hace auditables los
-- campos: guardar el subtotal es parte de poder verificar la lectura.
--
-- NULL y no cero a proposito: muchos comprobantes no traen subtotal impreso, y
-- un 0 inventado haria que la cuenta `subtotal + IVA == total` pareciera
-- cuadrar cuando en realidad no se sabe el subtotal. Un cero en esa columna
-- seria un dato falso con apariencia de dato.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS subtotal NUMERIC(12, 2);

-- ---------------------------------------------------------------------
-- 2. Constraints
-- ---------------------------------------------------------------------
--
-- DROP primero para poder corregir una version anterior de la misma
-- constraint sin acordarse del nombre.

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_spot_check_values;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_spot_check_values
    CHECK (
        spot_check_status IS NULL
        OR spot_check_status IN ('PENDIENTE', 'CORRECTO', 'INCORRECTO')
    );

-- Un veredicto sin fecha no se puede envejecer ni auditar. Y la age es lo que
-- permite medir a que ritmo se acumula la evidencia para certificar el 96%,
-- que es la funcion principal de esta tabla.
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_spot_check_verdict_has_date;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_spot_check_verdict_has_date
    CHECK (
        spot_check_status IS NULL
        OR spot_check_status = 'PENDIENTE'
        OR spot_checked_at IS NOT NULL
    );

-- No se puede muestrear un ticket que no fue automatico, ni uno que alguien ya
-- toco. La muestra existe para verificar lo que el sistema aprobo solo.
--
-- Solo AUTO_APROBADO, y no tambien APROBADO: un ticket que una persona
-- aprueba ya no mide el automatismo, mide a la persona. Y aunque nadie lo
-- hubiera corregido, meter un caso humano en el promedio de exactitud
-- inflaria el numero con lo que el sistema no hizo.
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_spot_check_only_auto;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_spot_check_only_auto
    CHECK (spot_check_status IS NULL OR extraction_status = 'AUTO_APROBADO');

-- ---------------------------------------------------------------------
-- 3. Indices
-- ---------------------------------------------------------------------
--
-- ix_tickets_spot_check_queue: parcial sobre PENDIENTE. La cola de muestreo no
-- tiene que ordenar sobre los tickets ya revisados, que crecen sin parar (a
-- diferencia de la cola de revision, que se vacia).
--
-- ix_tickets_spot_check_report: parcial sobre los que tienen veredicto. Es la
-- que sostiene la agregacion del reporte de exactitud, agrupada por
-- confidence_source. Sin el, el reporte recorre todo el historico para
-- contar unas cuantas filas.

CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_queue
    ON tickets (company_id, created_at)
    WHERE spot_check_status = 'PENDIENTE';

CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_report
    ON tickets (company_id, confidence_source)
    WHERE spot_check_status IS NOT NULL;

COMMIT;

-- ---------------------------------------------------------------------
-- 4. Verificacion
-- ---------------------------------------------------------------------
--
-- La constraint de valores tiene que existir y listar los mismos tres estados
-- que el enum `SpotCheckStatus` de app/core/enums.py. Si divergen, un estado
-- nuevo se escribe en Python y la base lo rechaza con un error que aparece en
-- produccion, en el momento de registrar una revision. El test
-- tests/unit/test_spot_check.py compara las dos listas.
--
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'tickets'::regclass AND conname LIKE 'ck_tickets_spot%';
--
--   SELECT indexname FROM pg_indexes
--    WHERE tablename = 'tickets' AND indexname LIKE 'ix_tickets_spot%';
--
-- Las de abajo tienen que salir vacias:
--
--   SELECT id, spot_check_status FROM tickets
--    WHERE spot_check_status IN ('CORRECTO', 'INCORRECTO')
--      AND spot_checked_at IS NULL;
--
--   SELECT id, spot_check_status, extraction_status FROM tickets
--    WHERE spot_check_status IS NOT NULL
--      AND extraction_status <> 'AUTO_APROBADO';
