CREATE TABLE IF NOT EXISTS auditoria (
    id BIGSERIAL PRIMARY KEY,
    usuario_id UUID NULL,
    rol VARCHAR(50) NULL,
    accion VARCHAR(100) NOT NULL,
    modulo VARCHAR(50) NULL,
    registro_afectado UUID NULL,
    detalles TEXT NULL,
    ip_inet VARCHAR(45) NULL,
    fecha TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria (fecha DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_usuario ON auditoria (usuario_id);
