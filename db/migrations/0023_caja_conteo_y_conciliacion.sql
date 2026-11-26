-- 0023_caja_conteo_y_conciliacion.sql
-- El cierre registra lo que una persona conte y la diferencia contra el saldo,
-- y los datos de banco dejan de poder escribirse en una reposicion en efectivo.
-- Aditivo: columnas nuevas y un CHECK nuevo, sin borrar nada.

ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS advertencia_duplicado_fiscal TEXT;

ALTER TABLE cajas_chicas
    ADD COLUMN IF NOT EXISTS efectivo_contado NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS diferencia_cierre NUMERIC(12, 2);

ALTER TABLE movimientos_caja
    DROP CONSTRAINT IF EXISTS ck_movimientos_caja_sin_banco_fuera_de_banco;

ALTER TABLE movimientos_caja
    ADD CONSTRAINT ck_movimientos_caja_sin_banco_fuera_de_banco
    CHECK (
        origen_fondo IS NOT DISTINCT FROM 'banco'
        OR (banco_nombre IS NULL AND referencia_bancaria IS NULL)
    );

INSERT INTO schema_migrations (nombre)
VALUES ('0023_caja_conteo_y_conciliacion.sql')
ON CONFLICT (nombre) DO NOTHING;
