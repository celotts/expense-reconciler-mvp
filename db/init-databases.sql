-- Crear bases separadas por ambiente (para volumen limpio/rebuild)
\connect postgres
CREATE DATABASE IF NOT EXISTS expense_db_dev;
CREATE DATABASE IF NOT EXISTS expense_db_qa;
-- En prod se queda expense_db (POSTGRES_DB default).
-- El seed de sos se ejecuta luego por la app.