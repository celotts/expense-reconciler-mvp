-- =====================================================================
-- 0002 - Gate de confianza, cola de revision e idempotencia
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- `db/init.sql` solo corre la PRIMERA VEZ que Postgres arranca con el volumen
-- vacio (docker-entrypoint-initdb.d). En cuanto hay datos, se deja de leer.
--
-- Eso significa que agregar columnas al modelo no crea nada en una base que
-- ya existe. Sin esta migracion, la aplicacion arranca bien y revienta en la
-- primera consulta de tickets con:
--
--     asyncpg.exceptions.UndefinedColumnError: column "tickets.extraction_status" does not exist
--
-- Los tests no lo detectan porque corren contra SQLite, que construye el
-- esquema desde el modelo. El DDL de abajo esta copiado del DDL que emite
-- SQLAlchemy para el modelo, verificado con:
--
--     python -c "from sqlalchemy.schema import CreateTable; ..."
--
-- COMO APLICARLA (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0002_confidence_gate.sql
--
-- Es idempotente: se puede correr las veces que haga falta. Si algo ya esta,
-- lo deja como esta.
--
-- COMO SABER SI LA NECESITAS:
--
--     docker exec expense_pgvector psql -U postgres -d expense_db \
--         -c "\d tickets" | grep extraction_status
--
-- Si no sale nada, la necesitas.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Las columnas nuevas
-- ---------------------------------------------------------------------
-- IF NOT EXISTS para que se pueda correr mas de una vez sin fallar.

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS confidence NUMERIC(4, 3);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS confidence_source VARCHAR(20);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(20);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS source_type VARCHAR(20);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS source_file VARCHAR(500);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS validation_errors TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(100);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS review_notes TEXT;

-- ---------------------------------------------------------------------
-- 2. Normalizar datos antes de decidir nada
-- ---------------------------------------------------------------------
-- El orden importa. Primero se limpian los datos que son ruido de esquema,
-- despues se decide el estado. Si se invirtiera, un ticket con IVA NULL
-- quedaria PENDIENTE por una razon tecnica que ya no existe, y en la cola
-- aparece un ticket que en realidad esta bien: la primera vez que alguien
-- mira la cola y ve cosas que no estan rotas, deja de creerle a la cola.

-- tax_amount admite NULL en la tabla vieja, pero el modelo lo declara NOT NULL
-- y ademas una constraint con NULL da NULL, que en SQL NO es falso: la
-- comprobacion pasaria sin comprobar nada. La proteccion se desactivaria en
-- silencio justo en los tickets automaticos.
UPDATE tickets SET tax_amount = 0.00 WHERE tax_amount IS NULL;
ALTER TABLE tickets ALTER COLUMN tax_amount SET NOT NULL;

-- company_id: el modelo lo declara NOT NULL y la FK ya es ON DELETE CASCADE.
-- Con la columna nullable, un ticket huerfano de una empresa borrada sobrevive
-- y aparece en listas de una empresa que ya no existe.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tickets WHERE company_id IS NULL) THEN
        RAISE EXCEPTION
            'Hay tickets sin company_id. Revisa y borra o reasigna esas filas antes de aplicar la migracion: %',
            (SELECT count(*) FROM tickets WHERE company_id IS NULL);
    END IF;
END $$;
ALTER TABLE tickets ALTER COLUMN company_id SET NOT NULL;

-- ---------------------------------------------------------------------
-- 3. Backfill de extraction_status
-- ---------------------------------------------------------------------
-- Los tickets que ya existian NUNCA pasaron por el gate. Marcarlos
-- AUTO_APROBADO seria inventar una validacion que no ocurrio.
--
-- Pero tampoco tiene sentido volcar todo el historico a la cola: la cola es
-- para lo que necesita una persona AHORA, y si aparece con 3000 tickets
-- historicos la primera vez, se ignora para siempre.
--
-- La salida: cada ticket preexistente se revisa con la misma aritmetica que
-- exige la constraint de abajo. Los que ya cuadraban quedan APROBADO; los que
-- no, PENDIENTE. No se declara valido nada que no haya pasado los checks.
--
-- Lo que no se puede determinar (de donde venia: captura manual o IA) se
-- deja en NULL, no se inventa. La columna es nullable justamente para eso.

UPDATE tickets SET
    extraction_status = CASE
        WHEN total_amount > 0
         AND tax_amount >= 0
         AND tax_amount <= total_amount
         AND provider_name IS NOT NULL
         AND btrim(provider_name) <> ''
         AND btrim(provider_name) <> 'Unknown Provider'
        THEN 'APROBADO'
        ELSE 'PENDIENTE'
    END
WHERE extraction_status IS NULL;

-- El motivo se escribe SIEMPRE, con los mismos nombres de check que usa el
-- gate. Un ticket en la cola sin motivo es un ticket que el revisor no puede
-- ni priorizar ni corregir, y a la segunda vez de mirar la cola la deja de
-- mirar.
UPDATE tickets SET validation_errors = sub.fallos
FROM (
    SELECT id,
           NULLIF(concat_ws('; ',
               CASE WHEN total_amount <= 0
                    THEN 'total_not_positive' END,
               CASE WHEN tax_amount < 0
                    THEN 'tax_negative' END,
               CASE WHEN tax_amount > total_amount
                    THEN 'tax_exceeds_total' END,
               CASE WHEN provider_name IS NULL
                       OR btrim(provider_name) = ''
                       OR btrim(provider_name) = 'Unknown Provider'
                    THEN 'provider_missing' END
           ), '') AS fallos
    FROM tickets
) AS sub
WHERE tickets.id = sub.id
  AND tickets.extraction_status IN ('PENDIENTE', 'REQUIERE_REVISION')
  AND sub.fallos IS NOT NULL;

-- Red de seguridad: si quedo algo en la cola sin motivo, se dice al menos que
-- es historico. Preferible "no se por que" a nada.
UPDATE tickets SET validation_errors = 'backfill: sin validacion registrada'
WHERE extraction_status IN ('PENDIENTE', 'REQUIERE_REVISION')
  AND validation_errors IS NULL;

-- confidence y confidence_source quedan en NULL a proposito: estos tickets
-- nunca pasaron por un gate, asi que no se les inventa una confianza. Un 0.000
-- inventado contaminaria el promedio de confianza de la IA, que es la metrica
-- que respalda el objetivo de exactitud.

-- ---------------------------------------------------------------------
-- 4. Defaults iguales a los del modelo
-- ---------------------------------------------------------------------
-- Server-side tambien, no solo en Python. Un INSERT hecho a mano por psql debe
-- comportarse como uno hecho por la API; si no, alguien inserta datos por la
-- via que nadie testeo.

ALTER TABLE tickets ALTER COLUMN extraction_status SET DEFAULT 'PENDIENTE';
ALTER TABLE tickets ALTER COLUMN extraction_status SET NOT NULL;

-- ---------------------------------------------------------------------
-- 5. Constraints: la ultima linea, donde el gate ya no puede opinar
-- ---------------------------------------------------------------------
-- La condicion es "si el ticket esta cerrado (AUTO_APROBADO o APROBADO),
-- entonces los datos tienen que ser aritmeticamente validos".
--
-- Y es condicional a proposito. Una constraint incondicional de total > 0
-- impidia GUARDAR un documento ilegible, que es exactamente lo que el estado
-- PENDIENTE existe para hacer. Con la constraint incondicional, una foto de
-- un papel quemado daba IntegrityError -> 500 -> el documento se perdia en
-- silencio, y nadie se enteraba de que existio.
--
-- Lo mismo con RECHAZADO: si la constraint lo alcanzara, un documento ilegible
-- no se podria ni guardar en la cola ni descartar, y la cola no se podria
-- vaciar nunca.
--
-- DROP primero para poder corregir una version anterior de la misma constraint
-- sin tener que acordarse del nombre.

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_total_positive_when_settled;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_total_positive_when_settled
    CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR total_amount > 0);

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_tax_non_negative_when_settled;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_tax_non_negative_when_settled
    CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount >= 0);

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_tax_lte_total_when_settled;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_tax_lte_total_when_settled
    CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount <= total_amount);

-- ---------------------------------------------------------------------
-- 5. Indices
-- ---------------------------------------------------------------------
-- ix_tickets_review_queue: parcial, solo sobre los estados abiertos. La cola no
-- tiene que ordenar sobre el historico de tickets ya cerrados.
--
-- ix_tickets_source_hash: UNICO y parcial sobre source_hash IS NOT NULL. Es lo
-- que hace idempotente la carga masiva: reintentar un lote no duplica gastos.
-- Unico solo cuando existe, porque los tickets manuales no tienen hash y
-- varios NULL en la misma columna no pueden violar unicidad.

CREATE INDEX IF NOT EXISTS ix_tickets_review_queue
    ON tickets (extraction_status, company_id)
    WHERE extraction_status IN ('REQUIERE_REVISION', 'PENDIENTE');

CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_source_hash
    ON tickets (source_hash)
    WHERE source_hash IS NOT NULL;

COMMIT;

-- ---------------------------------------------------------------------
-- 6. Verificacion
-- ---------------------------------------------------------------------
-- Los SELECT de abajo tienen que salir vacios. Si algo aparece, la
-- migracion esta a medias y hay que arreglarlo antes de arrancar la app.
--
--   SELECT extraction_status, count(*) FROM tickets GROUP BY 1;
--   SELECT id, provider_name, total_amount FROM tickets
--    WHERE extraction_status IN ('AUTO_APROBADO','APROBADO')
--      AND (total_amount <= 0 OR tax_amount < 0 OR tax_amount > total_amount);
--   SELECT source_hash, count(*) FROM tickets
--    WHERE source_hash IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
