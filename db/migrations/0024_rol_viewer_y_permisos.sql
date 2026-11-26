-- 0024_rol_viewer_y_permisos.sql
-- Anade `viewer`: un rol de SOLO LECTURA, por empresa como los demas.
--
-- No se crea una tabla de permisos: las acciones se derivan del rol en
-- `app/core/permisos.py` y se sirven en `GET /auth/empresas`. Guardarlas aqui
-- seria una segunda verdad que el front podria editar y el backend no.
-- El CHECK se reemplaza porque el anterior no admitia el nuevo valor; sin esto,
-- asignar `viewer` daria un 500 de base de datos en vez de una membresia.
-- Nadie pierde acceso con la migracion: los roles existentes siguen validos.

ALTER TABLE usuarios_empresas
    DROP CONSTRAINT IF EXISTS ck_usuarios_empresas_rol;

ALTER TABLE usuarios_empresas
    ADD CONSTRAINT ck_usuarios_empresas_rol
    CHECK (rol IN ('owner', 'admin', 'accountant', 'operator', 'viewer'));

INSERT INTO schema_migrations (nombre)
VALUES ('0024_rol_viewer_y_permisos.sql')
ON CONFLICT (nombre) DO NOTHING;
