-- =====================================================================
-- 0020 - Auditoria segura: columna resultado + tabla append-only
-- =====================================================================
--
-- - `auditoria.resultado` ('exito' / 'fallo', por defecto 'exito') para
--   distinguir un evento registrado de uno fallido sin borrar nada.
-- - La tabla es append-only: triggers que impiden UPDATE y DELETE, con el
--   mismo patron que `ticket_documents` (trg_ticket_documents_no_actualizar
--   / trg_ticket_documents_no_borrar en db/init.sql).
--
-- Idempotente (`IF NOT EXISTS` / `IF EXISTS` / `ON CONFLICT`) para poder
-- reejecutarse con seguridad.

-- ---------------------------------------------------------------------
-- 1. Columna resultado
-- ---------------------------------------------------------------------
ALTER TABLE auditoria ADD COLUMN IF NOT EXISTS resultado VARCHAR(20) NOT NULL DEFAULT 'exito';

ALTER TABLE auditoria DROP CONSTRAINT IF EXISTS ck_auditoria_resultado;
ALTER TABLE auditoria ADD CONSTRAINT ck_auditoria_resultado
    CHECK (resultado IN ('exito', 'fallo'));

COMMENT ON COLUMN auditoria.resultado IS
    'Resultado del evento auditado: exito o fallo. Por defecto exito; los intentos fallidos y errores de sistema se registran como fallo.';

-- ---------------------------------------------------------------------
-- 2. Append-only: ni UPDATE ni DELETE sobre auditoria
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auditoria_no_actualizar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'auditoria es append-only: un registro de auditoria no se actualiza. Id %',
        OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION auditoria_no_borrar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'auditoria es append-only: un registro de auditoria no se borra. Id %',
        OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auditoria_no_actualizar ON auditoria;
CREATE TRIGGER trg_auditoria_no_actualizar
    BEFORE UPDATE ON auditoria
    FOR EACH ROW EXECUTE FUNCTION auditoria_no_actualizar();

DROP TRIGGER IF EXISTS trg_auditoria_no_borrar ON auditoria;
CREATE TRIGGER trg_auditoria_no_borrar
    BEFORE DELETE ON auditoria
    FOR EACH ROW EXECUTE FUNCTION auditoria_no_borrar();

-- ---------------------------------------------------------------------
-- 3. Registro de migracion
-- ---------------------------------------------------------------------
INSERT INTO schema_migrations (nombre)
VALUES ('0020_auditoria_segura')
ON CONFLICT (nombre) DO NOTHING;
