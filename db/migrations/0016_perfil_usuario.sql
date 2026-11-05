-- 0016: agrega perfil/rol al usuario
--
-- Por que: el sistema de permisos definia roles en codigo (admin, supervisor,
-- consultor, digitalizador, qa) pero la base de datos no tenia columna para
-- almacenarlos. Toda cuenta leia 'digitalizador' por defecto. Se agrega la
-- columna con default 'digitalizador' para no romper cuentas existentes.

ALTER TABLE users ADD COLUMN IF NOT EXISTS perfil VARCHAR(50) NOT NULL DEFAULT 'digitalizador';
ALTER TABLE users ADD COLUMN IF NOT EXISTS grupo VARCHAR(50) NOT NULL DEFAULT 'operaciones';
