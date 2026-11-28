-- 0029_estado_empresa.sql
-- La empresa se DESACTIVA o se ELIMINA logicamente; nunca se borra su evidencia.
--
-- `companies` no tenia columna de estado, y el DELETE ofrecia una baja que no
-- existia: las FK son ON DELETE CASCADE y en Postgres un borrado se llevaba por
-- delante tickets, banco, caja, inventario y cierres.
--
-- `estado` tiene TRES valores, no un booleano: `inactivo` es una baja
-- reversible y `eliminado` es el borrado logico. Solo `activo` puede ser la
-- empresa activa de una peticion. La columna nace en `activo` para que una
-- base con datos siga trabajando despues de la migracion, igual que el
-- backfill de admin de `0022`. No se rellena nada del historico: es aditiva.

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS estado VARCHAR(20) NOT NULL DEFAULT 'activo'
    CHECK (estado IN ('activo', 'inactivo', 'eliminado'));

CREATE INDEX IF NOT EXISTS ix_companies_estado ON companies (estado);

COMMENT ON COLUMN companies.estado IS
    'activo permite trabajar; inactivo es baja reversible; eliminado es soft delete.';

INSERT INTO schema_migrations (nombre)
VALUES ('0029_estado_empresa.sql')
ON CONFLICT (nombre) DO NOTHING;
