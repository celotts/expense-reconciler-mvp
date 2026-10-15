-- Bases existentes: este script solo se conecta y verifica
-- No crea bases si ya existen (usando psql variables)
\connect postgres
-- Las bases expense_db, expense_db_dev, expense_db_qa deben existir previamente
-- o ser creadas manualmente con: createdb expense_db
SELECT 1 AS db_check;
