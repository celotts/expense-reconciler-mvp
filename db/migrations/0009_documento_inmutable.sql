-- db/migrations/0009_documento_inmutable.sql
--
-- EL DOCUMENTO DIGITALIZADO NO SE ALTERA. SE APILA.
--
-- QUE SE ARREGLA
-- -------------
-- `PUT /api/v1/tickets/{id}/documento` reemplazaba el comprobante original con
-- `DELETE` + `INSERT`. Los bytes originales desaparecian y no quedaba nada de
-- ellos: ni el hash anterior, ni quien lo cambio, ni cuando, ni por que. Y el
-- endpoint ni siquiera tomaba el usuario autenticado, asi que la pregunta
-- "quien sustituyo el papel de este gasto" no tenia respuesta.
--
-- Eso contradice la promesa central del producto: que el comprobante es la
-- evidencia contra la que se contrasta cualquier lectura. Si el papel se puede
-- borrar en silencio, el muestreo puede estar midiendo algo que nadie reviso, y
-- no hay forma de demostrarlo.
--
-- QUE HACE ESTA MIGRACION
-- -----------------------
-- 1. `ticket_documents` deja de ser "una fila por ticket" y pasa a ser una
--    cadena de versiones. Cada version apunta a la que reemplaza con
--    `reemplaza_a`, y hay exactamente UNA vigente (la que no fue reemplazada).
--    Un indice unico parcial lo garantiza a nivel de motor.
-- 2. Cada reemplazo guarda quien lo hizo y por que. Texto y no llave foranea,
--    por el mismo motivo que `tickets.spot_checked_by`: la firma tiene que
--    sobrevivir a la baja de la cuenta.
-- 3. Un trigger prohibe `UPDATE` y `DELETE` sobre la tabla. No es una regla del
--    codigo de aplicacion: es de la base, y por eso vale tambien para un `psql`,
--    una restauracion mal hecha o el proximo script que se escriba.
--
-- POR QUE EL TRIGGER DE DELETE DEJA PASAR ALGO
--
-- `ticket_documents.ticket_id` tiene `ON DELETE CASCADE`, asi que borrar un
-- ticket borra su documento, y borrar una empresa borra sus tickets. Un trigger
-- que lo prohibiera dejaria imposible borrar una empresa, que es una operacion
-- legitima (`DELETE /companies/{id}` la hace). Por eso el trigger mira si el
-- ticket padre SIGUE VIVO: si sigue ahi, el borrado es una intencion y se
-- rechaza; si ya no esta, es una cascada y se deja pasar. La distincion es
-- exactamente "borre el gasto entero" contra "me robe el papel".
--
-- LO QUE NO CAMBIA
-- ----------------
-- - `tickets.source_hash`: sigue siendo el hash de lo que el escaner leyo. Un
--   documento reemplazado NO lo cambia, porque cambiar el papel no es cambiar
--   la lectura (ver el docstring de `reemplazar_documento`).
-- - El endpoint que sirve el documento: sigue sirviendo el vigente, con la misma
--   lista cerrada de content-type.
-- - SQLite no tiene triggers de este tipo, asi que los tests que corren ahi
--   comprueban el servicio, NO el motor. Para el motor esta
--   `scripts/verify_postgres_documentos.py`.

-- ---------------------------------------------------------------------------
-- 1. La cadena de versiones
-- ---------------------------------------------------------------------------

-- El UNIQUE de `ticket_id` ya no puede existir: un ticket puede tener varias
-- versiones, y lo que se prohibe es que haya dos VIGENTES, no que haya dos.
ALTER TABLE ticket_documents DROP CONSTRAINT IF EXISTS ticket_documents_ticket_id_key;

ALTER TABLE ticket_documents
    ADD COLUMN IF NOT EXISTS reemplaza_a UUID REFERENCES ticket_documents(id) ON DELETE SET NULL;

ALTER TABLE ticket_documents
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE ticket_documents
    ADD COLUMN IF NOT EXISTS actor VARCHAR(255);

ALTER TABLE ticket_documents
    ADD COLUMN IF NOT EXISTS motivo TEXT;

-- Las constraints, en un DO para que la migracion sea RE-EJECUTABLE.
--
-- Postgres no tiene `ADD CONSTRAINT IF NOT EXISTS`, y un `ALTER TABLE ... ADD
-- CONSTRAINT` a pelo deja la migracion a medias si se corre dos veces: la
-- segunda falla en la primera constraint y las de abajo no se aplican. Eso
-- paso aqui, y el sintoma es una base con la mitad de las reglas.
--
-- `pg_constraint` es la fuente de verdad: se mira si el nombre ya existe antes
-- de anadirla. Es el mismo truco que usan las migraciones de este repo para
-- las columnas (`ADD COLUMN IF NOT EXISTS`).
DO $$
DECLARE
    regla TEXT;
    cuerpo TEXT;
BEGIN
    FOREACH regla IN ARRAY ARRAY[
        'ck_ticket_documents_version',
        'ck_ticket_documents_motivo_si_reemplaza',
        'ck_ticket_documents_actor_si_reemplaza'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = regla
        ) THEN
            CASE regla
                WHEN 'ck_ticket_documents_version' THEN
                    cuerpo := 'CHECK (version >= 1)';
                WHEN 'ck_ticket_documents_motivo_si_reemplaza' THEN
                    cuerpo := 'CHECK (reemplaza_a IS NULL OR (motivo IS NOT NULL AND length(trim(motivo)) > 0))';
                ELSE
                    cuerpo := 'CHECK (reemplaza_a IS NULL OR (actor IS NOT NULL AND length(trim(actor)) > 0))';
            END CASE;
            EXECUTE format(
                'ALTER TABLE ticket_documents ADD CONSTRAINT %I %s', regla, cuerpo
            );
        END IF;
    END LOOP;
END $$;

-- UNA version puede ser reemplazada por UNA sola siguiente. Este indice es lo
-- que impide que la cadena se BIFURQUE.
--
-- Y por eso el vigente NO se define como "la que tiene `reemplaza_a IS NULL`":
-- con la cadena append-only, la version 1 es la unica con `reemplaza_a IS NULL` y
-- lo sigue siendo para siempre, porque volverla a poner en NULL exigiria un
-- UPDATE y el UPDATE esta prohibido dos lineas mas abajo. El vigente es el de
-- mayor `version`, que es la punta de la cadena, y este indice garantiza que la
-- cadena es lineal y por lo tanto tiene una sola punta.
--
-- Este indice se creo al principio como `UNIQUE (ticket_id) WHERE reemplaza_a IS
-- NULL`, que es lo que aparece en una version previa de esta migracion y en
-- `init.sql`. Era el equivocado: no impedia la bifurcacion y una tercera version
-- que apuntara a la primera entraba sin problema. Verificado con
-- `scripts/verify_postgres_documentos.py` paso 5.
DROP INDEX IF EXISTS ix_ticket_documents_vigente;

CREATE UNIQUE INDEX IF NOT EXISTS ix_ticket_documents_sin_bifurcar
    ON ticket_documents (reemplaza_a)
    WHERE reemplaza_a IS NOT NULL;

-- El recorrido de la cadena, que es como la lee el informe de integridad.
CREATE INDEX IF NOT EXISTS ix_ticket_documents_cadena
    ON ticket_documents (ticket_id, version);

-- ---------------------------------------------------------------------------
-- 2. La regla del motor
-- ---------------------------------------------------------------------------

-- UPDATE: nunca. No hay un caso legitimo. Un documento es una foto de un papel;
-- cambiarlo in situ es alterar la evidencia, no corregirla.
CREATE OR REPLACE FUNCTION ticket_documents_no_actualizar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'ticket_documents es append-only: un documento no se actualiza, se agrega '
        'una version nueva (reemplaza_a) y la anterior se conserva. Ticket %',
        OLD.ticket_id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ticket_documents_no_actualizar ON ticket_documents;
CREATE TRIGGER trg_ticket_documents_no_actualizar
    BEFORE UPDATE ON ticket_documents
    FOR EACH ROW EXECUTE FUNCTION ticket_documents_no_actualizar();

-- DELETE: no, salvo que el ticket ya no exista ( cascada de un borrado de gasto
-- o de empresa). Ver la nota del encabezado.
CREATE OR REPLACE FUNCTION ticket_documents_no_borrar() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM tickets WHERE id = OLD.ticket_id) THEN
        RAISE EXCEPTION
            'ticket_documents es append-only: no se borra un documento mientras su '
            'ticket exista. Para cambiar el papel se agrega una version nueva. Ticket %',
            OLD.ticket_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ticket_documents_no_borrar ON ticket_documents;
CREATE TRIGGER trg_ticket_documents_no_borrar
    BEFORE DELETE ON ticket_documents
    FOR EACH ROW EXECUTE FUNCTION ticket_documents_no_borrar();