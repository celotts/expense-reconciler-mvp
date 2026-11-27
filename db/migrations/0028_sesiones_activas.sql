-- 0028_sesiones_activas.sql
-- Actividad por sesion, para caducar por INACTIVIDAD en el servidor.
--
-- `exp` sigue siendo la caducidad ABSOLUTA: 8 horas desde que se emitio el
-- token, y no se extiende nunca. Esta tabla responde a otra pregunta: "¿ha
-- dejado de usarla esta persona?". Son dos tiempos y no se pueden mezclar: con
-- uno solo, o un token robado vive 8 horas, o alguien que trabaja seguido
-- pierde la sesion a media jornada.
--
-- La fila es estado operativo, no evidencia. Se crea en la primera peticion
-- autenticada con un `jti` (los tokens anteriores a este cambio no lo traen),
-- se actualiza en cada peticion y se borra al cerrar sesion. La prueba de que
-- alguien cerro la sesion esta en `tokens_revocados`, que NO se borra.
--
-- Si la inactividad vence, la fila se deja QUIETA, sin actualizar: borrarla
-- haria que la peticion siguiente creara una fila nueva y el token volviera a
-- servir. El token no se anade a `tokens_revocados` por inactividad: no hubo
-- una decision humana de cerrar, y el motivo no esta en la lista de la 0025.

CREATE TABLE IF NOT EXISTS sesiones_activas (
    jti UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emitida_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expira_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ultima_actividad TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_sesiones_activas_usuario ON sesiones_activas (user_id);
CREATE INDEX IF NOT EXISTS ix_sesiones_activas_actividad ON sesiones_activas (ultima_actividad);

COMMENT ON COLUMN sesiones_activas.jti IS
    'El claim jti del token firmado. Ultima actividad del servidor por sesion.';

INSERT INTO schema_migrations (nombre)
VALUES ('0028_sesiones_activas.sql')
ON CONFLICT (nombre) DO NOTHING;
