-- =====================================================================
-- 0004 - Autenticacion: quien esta mirando esto
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Misma razon que 0002 y 0003: `db/init.sql` solo corre con el volumen vacio,
-- asi que agregar columnas al modelo no crea nada en una base que ya existe.
-- Sin esta migracion, la app levanta y revienta en la primera consulta con
-- UndefinedTable: "users".
--
-- QUE RESUELVE
--
-- La tabla de usuarios. Todo lo demas es codigo. Esto es lo unico que la base
-- necesita.
--
-- LO QUE ESTA MIGRACION NO HACE, A PROPOSITO
--
-- No siembra ningun usuario. Una contrasena inicial dentro de un .sql versionado
-- es una contrasena que todo el mundo que clone el repositorio conoce, y suele
-- sobrevivir al primer despliegue porque nadie se acuerda de cambiarla. El
-- primer usuario lo crea quien administra, en su terminal:
--
--   PYTHONPATH=. python3 scripts/crear_usuario.py --email yo@empresa.mx
--
-- El script pide la contrasena por teclado, no por argumento: una contrasena
-- en la linea de comandos queda en el historial del shell y en el `ps` de
-- cualquier otro proceso de la maquina.
--
-- SOBRE `last_login_at`
--
-- No se usa para decidir nada: el token ya dice si la sesion sigue viva. Se
-- escribe para que "quien toco esto y cuando" tenga una fuente que no sea el
-- log de la aplicacion, que se pierde en cada reinicio del contenedor.
--
-- ---------------------------------------------------------------------
-- Idempotente: se puede aplicar las veces que haga falta.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email          VARCHAR(255) NOT NULL,
    nombre         VARCHAR(120) NOT NULL,
    -- scrypt-v1$n$r$p$salt$hash. El formato lleva sus propios parametros para
    -- que subir el coste mas adelante no invalide las contrasenas ya guardadas.
    password_hash  VARCHAR(255) NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at  TIMESTAMP WITH TIME ZONE
);

-- El login es por correo. Tiene que ser unico, y el indice es el que lo
-- garantiza: sin el, dos personas podrian tener el mismo correo y el login
-- devolveria la cuenta que encontrara primero, que no es necesariamente la
-- que se quiso entrar.
--
-- Es UNIQUE y no un indice normal a proposito. Con un indice normal, un
-- segundo alta con el mismo correo pasaria y habria dos cuentas; la
-- contrasena correcta para una fallaria contra la otra, y nadie sabria
-- cual de las dos es la buena.
--
-- Y es sobre `lower(email)`, no sobre `email`. El login siempre normaliza a
-- minusculas antes de buscar (`LoginRequest.correo_normalizado`), asi que un
-- indice sobre la columna tal cual no protege de nada: "Ana@empresa.mx" y
-- "ana@empresa.mx" pasarian los dos, y el dia que alguien entre con cualquiera
-- de las dos cajas la consulta `WHERE lower(email) = ...` devolveria DOS filas.
-- `scalar_one_or_none()` lanza MultipleResultsFound sobre eso, que es un 500
-- en el login y no un mensaje de "ya existe". Solo un indice funcional
-- cierra las dos cosas a la vez: no deja crear el duplicado, y ademas sirve
-- para la consulta del login.
--
-- El indice parcial de abajo tambien va sobre `lower(email)`, por lo mismo.
--
-- SI ESTA CREACION FALLA con "Key (lower(email))=(...) is duplicated", no se
-- cambia el indice: hay dos cuentas que solo se diferencian en la caja del
-- correo. Se listan las dos, se decide cual se queda, se borra la otra y se
-- repite el script. Ojo con el orden: un indice sobre `lower()` no se puede
-- crear antes de limpiar los datos, mientras que un UNIQUE sobre la columna
-- suelta si se podria crear vacio y llenarse despues.
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (lower(email));

CREATE INDEX IF NOT EXISTS ix_users_activos
    ON users (lower(email))
    WHERE is_active;

COMMENT ON TABLE users IS
    'Cuentas con acceso. Sin autorregistro: las crea scripts/crear_usuario.py.';

COMMENT ON COLUMN users.password_hash IS
    'scrypt-v1$n$r$p$salt$hash. Nunca se serializa en ninguna respuesta de la API.';

-- ---------------------------------------------------------------------
-- Quien firmo cada veredicto
-- ---------------------------------------------------------------------
--
-- Ya existia `reviewed_by`, pero se llenaba con la cadena constante "user",
-- asi que las veinte revisiones de tres personas eran la misma fila repetida y
-- la columna no respondia a la pregunta para la que existe: "quien cambio
-- esto".
--
-- `spot_checked_by` es nuevo y es la misma idea para el muestreo. El reporte
-- de exactitud promedia veredictos de varias personas; sin saber cuales,
-- "el sistema es 96% exacto" es un promedio de opiniones sin dueño, y cuando
-- sale mal no hay a quien preguntarle.
--
-- El tipo es VARCHAR(255), el mismo que `users.email`. No es una llave foranea
-- a proposito: una baja es `is_active = false`, no un borrado, pero si alguien
-- llega a borrar la cuenta, el veredicto que firmo tiene que seguir ahí
-- diciendo quien fue. Una llave foranea con ON DELETE SET NULL perderia
-- justamente ese dato.
ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS spot_checked_by VARCHAR(255);

COMMENT ON COLUMN tickets.spot_checked_by IS
    'Correo de quien registro el veredicto. Texto, no llave: el veredicto sobrevive a la baja de la cuenta.';
