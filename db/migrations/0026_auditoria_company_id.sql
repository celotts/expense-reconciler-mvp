-- 0026_auditoria_company_id.sql
-- La empresa activa de cada evento de auditoria, para poder filtrar la exportacion.
--
-- `auditoria` guarda quien, que, cuando y resultado, pero no WHERE: sin la
-- empresa, exportar "solo esta empresa" obligaria a adivinarla desde
-- `registro_afectado` (un UUID de ticket no dice de que empresa es sin consultar
-- otra tabla, y un evento de login no tiene registro afectado).
--
-- NO HAY BACKFILL, y no es prudencia: `auditoria` es append-only por trigger
-- desde 0020, asi que un `UPDATE` para rellenar las filas viejas revienta. Apagar
-- el trigger para backfillear seria saltarse la regla que hace que la auditoria
-- sirva. Las filas anteriores quedan en NULL, que significa "no se registro",
-- no "pertenece a todas". La exportacion tiene que decir cuantas filas sin
-- empresa se omiten cuando se filtra, en vez de callarlas.

ALTER TABLE auditoria
    ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_auditoria_company_fecha
    ON auditoria (company_id, fecha DESC);

COMMENT ON COLUMN auditoria.company_id IS
    'Empresa activa (X-Company-ID) en la peticion que produjo el evento. NULL en eventos anteriores a 0026: dato no medido, no todas las empresas.';

INSERT INTO schema_migrations (nombre)
VALUES ('0026_auditoria_company_id.sql')
ON CONFLICT (nombre) DO NOTHING;
