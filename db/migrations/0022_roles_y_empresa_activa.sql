-- 0022_roles_y_empresa_activa.sql
-- Membresia usuario-empresa con rol por empresa.
--
-- El rol vive AQUI y no en `users` ni en el JWT: una misma cuenta puede ser
-- `operator` en una empresa y `admin` en otra, y la autorizacion se resuelve en BD en
-- cada peticion, igual que `is_active`. Un claim firmado no puede subir de rol.
-- La autorizacion no se guarda en el token por la misma razon.
--
-- Backfill: todos los usuarios existentes quedan como `admin` en TODAS las
-- empresas. No es prudencia, es continuidad: antes no habia restriccion alguna,
-- y dejar a alguien sin acceso al aplicar la migracion seria romper lo que ya
-- funcionaba. La decision de bajar a `operator` se toma despues, de forma
-- explicita, por empresa.

CREATE TABLE IF NOT EXISTS usuarios_empresas (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    rol VARCHAR(20) NOT NULL DEFAULT 'operator',
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, company_id),
    CONSTRAINT ck_usuarios_empresas_rol
        CHECK (rol IN ('owner', 'admin', 'accountant', 'operator'))
);

CREATE INDEX IF NOT EXISTS ix_usuarios_empresas_company_rol
    ON usuarios_empresas (company_id, rol)
    WHERE activo;

-- Continuidad: lo que antes estaba permitido para cualquier cuenta autenticada
-- sigue permitido para esa misma cuenta. `ON CONFLICT DO NOTHING` respeta una
-- membresia que alguien ya hubiera sembrado con otro rol.
INSERT INTO usuarios_empresas (user_id, company_id, rol)
SELECT u.id, c.id, 'admin'
FROM users u
CROSS JOIN companies c
ON CONFLICT (user_id, company_id) DO NOTHING;

INSERT INTO schema_migrations (nombre)
VALUES ('0022_roles_y_empresa_activa.sql')
ON CONFLICT (nombre) DO NOTHING;
