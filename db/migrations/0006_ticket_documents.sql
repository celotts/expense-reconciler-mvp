-- =====================================================================
-- 0006 - El comprobante original, que se estaba perdiendo
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Misma razón que 0002, 0003 y 0005: `db/init.sql` solo corre con el volumen
-- vacío. Agregar una tabla al modelo no crea nada en una base que ya existe.
--
-- EL PROBLEMA
--
-- Al subir un comprobante, el sistema leía los bytes, sacaba cinco campos y
-- tiraba el archivo. De la subida solo quedaban el nombre (`source_file`), el
-- hash (`source_hash`) y el texto que la lectura había sacado (`raw_text`).
--
-- Un ticket ilegible se conservaba, con su motivo en la cola, pero el papel del
-- que venía no existía en ningún lado. Tres cosas quedaban imposibles:
--
--   1. El muestreo de exactitud no se podía hacer. `SpotCheckRequest` pregunta
--      si la extracción coincidió con el papel; para responder hay que ver el
--      papel. Sin el archivo, el revisor decide a ciegas y el veredicto no mide
--      la exactitud: mide lo que recuerda del ticket.
--   2. La cola de revisión decía "corrige contra el documento original" sin
--      documento original.
--   3. No había reintento. Un PDF que no se leyó porque el extractor estaba
--      apagado quedaba ilegible para siempre, aunque al día siguiente se
--      encendiera.
--
-- POR QUÉ LOS BYTES VAN EN LA BASE Y NO EN EL DISCO
--
-- Tres razones, todas sobre la misma: la fila y el archivo tienen que ser lo
-- mismo, no dos cosas que puedan separarse.
--
-- - La copia de seguridad es una sola. Con el archivo en disco y los datos en
--   Postgres, respaldar el histórico sin los comprobantes deja una base que no
--   se puede auditar, y la promesa del producto es que sí se puede.
-- - No hay dos caminos que divergan. Un archivo huérfano, o una fila importada
--   en otra máquina cuyo archivo no existe, son estados que esta tabla no tiene.
-- - El borrado es una consecuencia, no una tarea. El CASCADE borra el documento
--   con el ticket; con un archivo en disco hay que acordarse de limpiarlo, y
--   lo que no se recuerda se queda.
--
-- El costo es real: la base crece con los documentos, y 10 MB por comprobante es
-- mucho más que los ~200 bytes de los datos del ticket. Quien prefiera un
-- almacenamiento de objetos (S3, MinIO) cambia esta tabla y nada más: el servicio
-- de `app/services/document_service.py` es el único que habla con ella.
--
-- POR QUÉ `content_type` NO SE USA TAL CUAL AL SERVIR
--
-- La columna guarda lo que declaró el cliente, pero el endpoint lo sirve desde
-- una lista cerrada. Servir `text/html` con bytes que el sistema no ha revisado
-- convierte el endpoint en XSS servido desde el propio dominio, con sesión
-- iniciada, disparado por alguien de confianza de la empresa. `image/svg+xml`
-- es lo mismo con superpoderes de dibujo. Ver
-- `app.models.ticket_document.content_type_servible`.
--
-- COMO APLICARLA (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0006_ticket_documents.sql
--
-- Es idempotente: se puede correr las veces que haga falta.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS ticket_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- UNIQUE y a la vez llave foránea. Las dos cosas dicen lo mismo desde dos
    -- ángulos: UNIQUE impide que un ticket tenga dos documentos (que haría que
    -- "el documento de este ticket" no tuviera respuesta única), y el CASCADE
    -- hace que borrar el ticket borre el comprobante, para que no queden
    -- comprobantes de gastos que ya no existen.
    ticket_id UUID NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,

    -- Los bytes. BYTEA llega a 1 GB por campo; el tope de 10 MB lo pone
    -- `TICKET_MAX_BYTES`, que es una regla de negocio y no del motor.
    contenido BYTEA NOT NULL,

    -- Lo declaró el cliente. Se usa para el nombre del archivo y como pista de
    -- lo que se guardó, no para decidir cómo se sirve.
    content_type VARCHAR(120),
    nombre_archivo VARCHAR(500),
    tamano INTEGER NOT NULL,

    -- El mismo SHA-256 que está en `tickets.source_hash`. Se repite aquí para
    -- poder verificar la integridad de lo guardado sin volver a pedirle el
    -- archivo a quien lo subió. Es verificable, no decorativo.
    sha256 VARCHAR(64),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- El índice de la llave foránea. `UNIQUE` ya crea uno, y este es el que permite
-- el CASCADE en cascada de un `DELETE` masivo sobre tickets: sin él, Postgres
-- va tabla por tabla.
CREATE INDEX IF NOT EXISTS ix_ticket_documents_ticket
    ON ticket_documents (ticket_id);

COMMIT;

-- ---------------------------------------------------------------------
-- Verificación
-- ---------------------------------------------------------------------

-- La tabla tiene que existir con sus seis columnas:
--
--   SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_name = 'ticket_documents' ORDER BY ordinal_position;
--
-- UNIQUE en ticket_id, y CASCADE. Esto es lo que impide que un ticket tenga dos
-- documentos y lo que hace que borrar el ticket borre el comprobante:
--
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'ticket_documents'::regclass;
--
-- Debe salir una FK con ON DELETE CASCADE. Si saliera SET NULL o nada, borrar
-- un ticket dejaría comprobantes de gastos que ya no existen.
--
-- Esta debe FALLAR, porque el ticket_id ya existe:
--
--   INSERT INTO ticket_documents (ticket_id, contenido, tamano)
--   SELECT id, '\x00'::bytea, 1 FROM tickets LIMIT 1;
--
-- Y esta debe funcionar, y es el caso que importa: un comprobante que se sube
-- después de que el ticket ya existía (el reemplazo del endpoint PUT).
--
--   INSERT INTO ticket_documents (ticket_id, contenido, tamano)
--   SELECT id, '\x25504446'::bytea, 4 FROM tickets
--    WHERE id NOT IN (SELECT ticket_id FROM ticket_documents) LIMIT 1;
--
-- El CASCADE. Esto tiene que dejar la tabla de documentos vacía:
--
--   BEGIN;
--   CREATE TEMP TABLE _vd AS SELECT id FROM tickets LIMIT 1;
--   DELETE FROM tickets WHERE id IN (SELECT id FROM _vd);
--   SELECT count(*) FROM ticket_documents
--    WHERE ticket_id IN (SELECT id FROM _vd);   -- tiene que dar 0
--   ROLLBACK;
--
-- Si da 1, el borrado del ticket deja el comprobante atrás, y la base acumula
-- archivos de gastos que ya no existen.
