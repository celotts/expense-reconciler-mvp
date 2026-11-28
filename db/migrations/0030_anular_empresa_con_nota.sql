-- 0030_anular_empresa_con_nota.sql
-- "Anular" es la etiqueta de la baja reversible (`inactivo`) en la interfaz.
-- Una empresa anulada conserva su evidencia, pero no puede procesar nada.
--
-- La nota NO se guarda solo en auditoria: quien mira la empresa necesita saber
-- POR QUE esta anulada sin leer el log. Quien firma y cuando se guardan en la
-- propia fila para que el cartel de la pantalla pueda decirlo. La auditoria
-- sigue guardando el cambio de estado; esto es el motivo legible, no el rastro.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS motivo_baja VARCHAR(500),
    ADD COLUMN IF NOT EXISTS bajada_en TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS bajada_por VARCHAR(150);

COMMENT ON COLUMN companies.motivo_baja IS
    'Motivo declarado al anular la empresa; se conserva mientras este inactiva.';

INSERT INTO schema_migrations (nombre)
VALUES ('0030_anular_empresa_con_nota.sql')
ON CONFLICT (nombre) DO NOTHING;
