-- 0025_tokens_revocados.sql
-- Lista persistida de tokens revocados.
--
-- Un JWT HS256 no se puede deshacer: si se roba, vive hasta `exp`. Antes no habia
-- `jti` ni lista, asi que logout, cambio de clave y reset administrativo solo
-- cambiaban la pantalla y el token segia sirviendo. Aqui se guarda el `jti` para
-- que la guardia global pueda rechazarlo en CUALQUIER router protegido.
--
-- La fila es un HECHO: alguien revoco este token en este instante. Por eso la
-- tabla es append-only por trigger, igual que `auditoria` y `ticket_documents`.
-- Borrar una fila reviviria un token robado, y actualizarla permitiria cambiar
-- el motivo despues de firmada.
--
-- `expires_at` no es para limpiar: la tabla no se limpia. Es el dato de cuando
-- caducaba el token que se revoco, para que el registro baste por si mismo.

CREATE TABLE IF NOT EXISTS tokens_revocados (
    jti UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    motivo VARCHAR(40) NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tokens_revocados_usuario
    ON tokens_revocados (user_id);

ALTER TABLE tokens_revocados DROP CONSTRAINT IF EXISTS ck_tokens_revocados_motivo;
ALTER TABLE tokens_revocados ADD CONSTRAINT ck_tokens_revocados_motivo
    CHECK (motivo IN ('logout', 'cambio_contrasena', 'reset_administrativo', 'baja_cuenta'));

COMMENT ON COLUMN tokens_revocados.jti IS
    'El claim jti del token firmado. Su existencia en esta tabla invalida el token antes de exp.';

-- Append-only: ni UPDATE ni DELETE. `ON DELETE RESTRICT` dice lo mismo desde la FK:
-- una revocacion sobrevive incluso a la baja de la cuenta (que ademas nunca borra filas).
CREATE OR REPLACE FUNCTION tokens_revocados_no_actualizar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'tokens_revocados es append-only: una revocacion no se actualiza. Jti %',
        OLD.jti
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION tokens_revocados_no_borrar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'tokens_revocados es append-only: una revocacion no se borra. Jti %',
        OLD.jti
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tokens_revocados_no_actualizar ON tokens_revocados;
CREATE TRIGGER trg_tokens_revocados_no_actualizar
    BEFORE UPDATE ON tokens_revocados
    FOR EACH ROW EXECUTE FUNCTION tokens_revocados_no_actualizar();

DROP TRIGGER IF EXISTS trg_tokens_revocados_no_borrar ON tokens_revocados;
CREATE TRIGGER trg_tokens_revocados_no_borrar
    BEFORE DELETE ON tokens_revocados
    FOR EACH ROW EXECUTE FUNCTION tokens_revocados_no_borrar();

INSERT INTO schema_migrations (nombre)
VALUES ('0025_tokens_revocados.sql')
ON CONFLICT (nombre) DO NOTHING;
