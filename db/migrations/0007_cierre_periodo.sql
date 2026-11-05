-- =====================================================================
-- 0007 - El periodo que alguien firmo como cerrado
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Misma razon que 0002, 0003, 0005 y 0006: `db/init.sql` solo corre con el
-- volumen vacio. Agregar la tabla al modelo no crea nada en una base que ya
-- existe, y sin esta migracion la app levanta y revienta en la primera consulta
-- con UndefinedTableError.
--
-- EL PROBLEMA
--
-- R3 del contrato (docs/contrato-producto.md:176) dice que el informe de cierre
-- mensual no puede declarar un periodo cerrado si hay tickets sin categoria o sin
-- conciliar. Y anade la excepcion: se puede, "salvo que el contador lo marque
-- explicitamente (y entonces el informe registra que lo fue)".
--
-- La excepcion era la parte que no existia. Se verifico que en los modelos no
-- hay estado de periodo cerrado, ni columna de quien lo cerro, ni tabla que lo
-- guarde. R3 se cumplia a medias: el informe puede negarse a declarar cerrado un
-- periodo con pendientes, pero no hay forma de registrar que un contador lo
-- cerro a sabiendas, y por lo tanto el informe no tiene nada que decir cuando si
-- lo fue.
--
-- QUE RESUELVE
--
-- Una fila por (empresa, periodo): quien lo cerro, cuando, que estaba pendiente
-- en ese momento, y la huella del informe que vio. Con eso R3 tiene las dos
-- mitades: el informe bloquea por defecto, y declara el cierre con su responsable
-- cuando existe la marca.
--
-- LO QUE ESTA MIGRACION NO HACE, A PROPOSITO
--
-- No congela el informe. "Un periodo cerrado genera su informe y ese informe es
-- inmutable" es D5, y D5 es Fase 4: congelar cada numero significaria reemitir
-- el informe cada vez que se corrige un ticket. Aqui se guarda la huella, que es
-- lo barato de D5 y lo que mas duele perder — con la huella, un periodo firmado
-- cuyos datos cambiaron se puede detectar; sin ella, no hay forma.
--
-- Tampoco es un historico de reaperturas. Una fila por (empresa, periodo), que
-- se sobrescribe. Un log de eventos es la respuesta correcta cuando se sepa que
-- reabrir es un flujo real; armarlo ahora seria construir Fase 4 adivinando.
--
-- No marca ningun periodo preexistente como cerrado. Esos periodos nunca pasaron
-- por una decision de cierre, y marcarlos seria inventar la firma de un contador.
-- Mismo criterio que 0002 con `confidence` y que 0003 con el muestreo: lo que no
-- se sabe se queda en NULL, y el informe lo dice en vez de rellenarlo.
--
-- POR QUE `cerrado_por` ES TEXTO Y NO LLAVE FORANEA
--
-- Copia el precedente de `tickets.spot_checked_by` (db/init.sql:83-85): "Quien
-- registro el veredicto, desde el token. Texto y no llave foranea a proposito: el
-- veredicto tiene que sobrevivir a la baja de la cuenta." Aplica igual y con mas
-- fuerza — un cierre fiscal que desaparece porque se dio de baja una cuenta es
-- peor que un veredicto que desaparece. El nombre es la prueba de que alguien se
-- hizo cargo, y una prueba que se borra sola no es una prueba.
--
-- POR QUE EL FORMATO ESTA REPLICADO CON OPERADORES DISTINTOS
--
-- Aqui, en Postgres, el periodo se valida con la regex del contrato:
-- ^\d{4}-(0[1-9]|1[0-2])$
--
-- En el modelo (app/models/cierre_periodo.py) NO se puede usar `~`: en SQLite
-- `~` es el operador de negacion a bits, no regex. Los tests corren contra
-- SQLite, asi que una constraint con `~` en el modelo no validaria nada ahi. Alla
-- se comprueba largo, guion en la posicion 5 y mes entre '01' y '12', que es lo
-- que se puede expresar igual en los dos motores. El anio de cuatro digitos lo
-- exige esta constraint de Postgres y el borde de la API (422 antes de la base).
--
-- COMO APLICARLA (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0007_cierre_periodo.sql
--
-- Es idempotente: se puede correr las veces que haga falta.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- 1. La tabla
-- ---------------------------------------------------------------------
--
-- El periodo es VARCHAR(7) y no DATE. Podria ser un DATE (el dia 1 del mes) y
-- parece mas tipado, pero entonces el informe tendria que decidir si el dia que
-- llega significa "enero" o "enero a partir del dia 1", y son dos cosas
-- distintas en un cierre fiscal. 'YYYY-MM' no admite esa lectura ambigua.

CREATE TABLE IF NOT EXISTS cierres_periodo (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    periodo VARCHAR(7) NOT NULL,
    cerrado_por VARCHAR(255) NOT NULL,
    cerrado_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Que estaba pendiente cuando se cerro. Ver el docstring del modelo: JSON y
    -- no tres columnas porque se muestra, no se agrega.
    pendientes_al_cerrar JSONB,

    -- SHA-256 de la serializacion canonica del informe en el momento del
    -- cierre. 64 hex, que es lo que produce hashlib.sha256().hexdigest().
    huella VARCHAR(64)
);

-- ---------------------------------------------------------------------
-- 2. Constraints
-- ---------------------------------------------------------------------

-- DROP primero para poder corregir una version anterior de la misma constraint
-- sin acordarse del nombre.
--
-- La regex del contrato, aqui con todo su poder porque esto si es Postgres. Lo
-- que no deja pasar: '2026-1', '2026-13', '2026-00', '26-01', '2026_01' y
-- cualquier cosa con basura dentro.

ALTER TABLE cierres_periodo DROP CONSTRAINT IF EXISTS ck_cierres_periodo_formato;
ALTER TABLE cierres_periodo ADD CONSTRAINT ck_cierres_periodo_formato
    CHECK (periodo ~ '^\d{4}-(0[1-9]|1[0-2])$');

-- Un periodo cerrado sin fecha no se puede envejecer ni auditar. Y la fecha es
-- lo que permite distinguir "cerrado en enero" de "cerrado hace un ano y nadie
-- lo ha vuelto a tocar".
--
-- La fecha NO puede estar en el futuro: un cierre firmado mañana no es un
-- hecho, es una intencion, y el informe no debe reportarlo como cierre.
ALTER TABLE cierres_periodo DROP CONSTRAINT IF EXISTS ck_cierres_periodo_sello_sano;
ALTER TABLE cierres_periodo ADD CONSTRAINT ck_cierres_periodo_sello_sano
    CHECK (cerrado_at <= now());

-- La huella tiene que ser una huella: 64 hex. Sin esto, un valor de mas se
-- acepta y la comparacion posterior nunca coincide, que es un fallo silencioso
-- (el informe siempre dira "cerrado con otra informacion", o nunca).
--
-- ^[0-9a-f]{64}$ en vez de {64}: hashlib produce minusculas, y una huella en
-- mayusculas es la misma huella escrita por otra persona.
ALTER TABLE cierres_periodo DROP CONSTRAINT IF EXISTS ck_cierres_periodo_huella_hex;
ALTER TABLE cierres_periodo ADD CONSTRAINT ck_cierres_periodo_huella_hex
    CHECK (huella IS NULL OR huella ~ '^[0-9a-f]{64}$');

-- ---------------------------------------------------------------------
-- 3. Indices
-- ---------------------------------------------------------------------
--
-- UNIQUE (company_id, periodo), y no PK compuesta, porque el grano del informe
-- es (empresa, periodo) y tener dos cierres del mismo periodo seria ambiguityo:
-- ¿cual de los dos es el que se firma? La fila mas reciente es la que aplica,
-- pero "la fila mas reciente de un conjunto que no deberia existir" es una
-- respuesta que depende de un ORDER BY que alguien puede olvidar.
--
-- Este indice tambien es el acceso principal: el informe siempre pregunta por un
-- (empresa, periodo) concreto.

CREATE UNIQUE INDEX IF NOT EXISTS ix_cierres_periodo_unico
    ON cierres_periodo (company_id, periodo);

COMMIT;

-- ---------------------------------------------------------------------
-- 4. Verificacion
-- ---------------------------------------------------------------------
--
-- La constraint de formato tiene que existir con la regex del contrato. Si
-- alguien la reescribe en el modelo para "simplificar", esta consulta dice si la
-- version de Postgres sigue siendo la completa.
--
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'cierres_periodo'::regclass
--      AND conname LIKE 'ck_cierres_periodo%';
--
--   SELECT indexname FROM pg_indexes
--    WHERE tablename = 'cierres_periodo';
--
-- Las tres de abajo tienen que dar ERROR (o 0 filas), nunca aceptar la fila:
--
--   INSERT INTO cierres_periodo (company_id, periodo, cerrado_por)
--    VALUES ((SELECT id FROM companies LIMIT 1), '2026-13', 'prueba');
--
--   INSERT INTO cierres_periodo (company_id, periodo, cerrado_por)
--    VALUES ((SELECT id FROM companies LIMIT 1), '2026-1', 'prueba');
--
--   INSERT INTO cierres_periodo (company_id, periodo, cerrado_por, huella)
--    VALUES ((SELECT id FROM companies LIMIT 1), '2026-01', 'prueba', 'NOESUNAHUELLA');
--
-- Y esta tiene que dar 0 filas (huella en mayusculas):
--
--   SELECT id FROM cierres_periodo WHERE huella ~ '[A-Z]';
