# Prompt — Cerrar la brecha entre lo que construiste y lo que la gente usa

> Este archivo es un prompt reutilizable. Cópialo tal cual, o pégalo en un agente con el
> contexto ya cargado (`AGENTS.md` se carga solo en la raíz del repo).
>
> **Cómo usarlo:** abre la sesión en la raíz del repo, pega el bloque `## PROMPT` de abajo.
> Los bloques anteriores a ese son la nota de diseño, para humanos.

---

## Nota de diseño (humano)

**El diagnóstico:** a la IA no le falta integración. Funciona y es rápida (probado: PDF real,
`AUTO_APROBADO`, `confidence 0.970`, 0.18 s, en local con `qwen2.5:3b` + `moondream`).

Lo que falta es que **la IA sea responsable de sus resultados y el usuario lo vea**.

La brecha es entre dos cosas:

- Lo que el código sabe hacer: gate de confianza, muestreo del 5%, veredictos firmados,
  evidencia declarada `INCONCLUYENTE` cuando no alcanza.
- Lo que la interfaz permite ejercer: crear tickets por captura manual, que nunca pasan
  por el gate, nunca llevan `confidence`, nunca entran al muestreo.

**La diferencia de producto frente a la competencia no es "usamos IA", es "no afirmamos
lo que no podemos sostener".** Un competidor dice "sube tu recibo y lo leo con IA". Este
producto puede decir: cada lectura se verifica aritméticamente antes de mirar la
confianza, y el 5% se revisa a mano para dar el número real — o decir que no hay evidencia.

**Ese argumento solo es creíble si el usuario puede verlo funcionar.** Hoy no lo ve.

### Por qué pgvector no entra en este prompt

Verificado en la base: extensión `vector` 0.8.6 instalada, **0 columnas** de tipo vector,
**0 índices** ivfflat/hnsw, **0 llamadas** a `create_embedding()`. Es adorno.

Conciliar es consulta numérica (monto, fecha) y va perfecta con índices B-tree. El único
uso honesto de embeddings en este dominio sería **duplicados por contenido**: el SHA-256
dice "mismo archivo", no "mismo gasto" — hoy un comprobante escaneado dos veces con
distinta resolución crea dos tickets. Se deja para después, y antes está
`VendorNormalizer` (~60 alias en `app/services/ai_client.py:246-350`), que está escrito y
muerto, y arregla "Oxxo"/"OXXO"/"OXXO EXPRESS" como tres proveedores distintos.

---

## PROMPT

Trabaja en `/Users/carloslott/develop/python/expense-reconciler-mvp`.

**Lee primero, en este orden:** `AGENTS.md`, `docs/captura.md`, `docs/testing.md`,
`docs/known-issues.md`. Los tres primeros son la fuente de verdad y ya están verificados
contra el código. Si algo contradice al código, el código gana: dilo y corrige el doc.

### Objetivo

Que la lectura por IA sea un camino de primera clase en la interfaz, con su veredicto
visible, y que el muestreo de exactitud sea medible por el mismo camino que el usuario
usa. El producto vende "la IA no se inventa la confianza"; hay que poder demostrarlo.

Hay **3 tareas, en orden**. Haz la 1 y verify. La 2 depende de la 1. La 3 es opcional y
va al final. **No adelantes tareas ni combines commits.**

---

### TAREA 1 — Dos vías de captura + veredicto visible

**Contexto:** hoy la UI hace `POST /tickets/extract` (solo preview, no persiste) y luego
`POST /tickets/`, que es captura **manual**: nace con `source_type=MANUAL`,
`confidence=NULL` y **no entra al muestreo del 5%** (el muestreo solo se dispara si el
veredicto fue `AUTO_APROBADO`, `app/api/tickets.py:280-285`). El comprobante original ya
se adjunta (`pendingFile` + `subirDocumento`, added en el fix anterior).

`extractAndCreate` ya existe en `front/src/services/api.ts:320-329` y **nadie la llama**.

**Qué hacer:**

1. En el modal de extracción (`front/src/pages/Tickets.tsx`), detect si el usuario
   **aceptó la lectura sin editarla** comparando el formulario contra la extracción.
   - Aceptada tal cual → `ticketsApi.extractAndCreate(file, companyId, fileType)`.
     Esto pasa por el gate: `confidence` y `confidence_source` reales, y entra al muestreo.
   - Editada → el flujo actual `create` + `subirDocumento`. Se queda como captura manual
     **a propósito**: si el usuario corrigió a la IA, ya no es lectura automática y no
     debe contar como tal en el reporte de exactitud.
   - Si el usuario edita, muestra por qué: "Como lo corregiste, este ticket se registra
     como captura manual y no cuenta para la medición de exactitud". Sin eso parece un bug.

2. Muestra el veredicto. En la ficha del ticket y en la cola de revisión, muestra
   `extraction_status`, `confidence` y `confidence_source` con su traducción en español
   (ya existen etiquetas en `front/src/utils/validation.ts`; extiéndelas si faltan).
   - `AUTO_APROBADO` → "IA · 0.97 · la aritmética cuadra y la lectura es segura"
   - `REQUIERE_REVISION` → "va a revisión: <motivo concreto, no 'confianza baja'>"
   - `PENDIENTE` → "falta <campo bloqueante>"
   El `validation_errors` ya trae los motivos con los números comparados
   (`confidence_gate.py:126-130`). Muéstralos.

3. Si `extractAndCreate` devuelve 4xx, degrada al camino manual en vez de dejar al usuario
   sin ticket. El ticket ya guardado con comprobante es mejor que una lectura perfecta
   perdida.

**Restricciones:**
- No quites el paso de revisión humana. Es deliberado: el usuario confirma antes de que
  exista un registro contable.
- No cambies el backend salvo que encuentres un bloqueo real. Si lo encuentras, dilo antes
  de tocarlo.
- El `File` debe seguir adjuntándose. Perder el comprobante es recuperable; perder el
  ticket no. Mantén el try/except propio de `subirDocumento` y el aviso en ámbar.

---

### TAREA 2 — El 96% medible de verdad

**Contexto:** hoy hay 14 veredictos de muestreo y el reporte sale `INCONCLUYENTE`. Con
tasa 5% hacen falta del orden de 300 veredictos para una conclusión. El reporte ya
distingue bien `CUMPLE` / `NO_CUMPLE` / `INCONCLUYENTE` / `SIN_EVIDENCIA`.

**No cambies la tasa, el umbral ni la lógica del reporte.** El sistema es honesto y eso
es el activo. Lo que falta es que la Tarea 1 produzca lecturas muestreables por el camino
que la gente usa.

**Qué hacer:**
- Verifica que un ticket creado por `extractAndCreate` entra efectivamente al muestreo.
- Si algo lo bloquea, arréglalo.
- Deja escrito (en `docs/known-issues.md`) cuántos veredictos faltan para cada veredicto
  posible, para que sepas cuándo puedes afirmarlo y cuándo no. Si ya está, verifícalo y
  no lo reescribas.

---

### TAREA 3 — Solo si sobra tiempo: `VendorNormalizer`

Está escrito en `app/services/ai_client.py:246-350` (~60 alias) y no se ejecuta: su único
referente era `app/modules/expenses/services/pipeline.py`, que era código muerto y **ya no
existe** — se borró entero (ver `docs/known-issues.md` §3). Así que hoy no tiene ni un
referente, y el siguiente que lo lea no lo encontrará buscándolo en el código.

**Conéctalo a la ruta viva** de creación de tickets, con test. Opcionalmente añade
detección de duplicados por contenido para el caso "mismo comprobante, distinta resolución"
(solución sin pgvector: comparar `raw_text` normalizado, o un hash de la extracción
semántica). **No construyas pgvector.**

---

### Disciplina — obligatoria

Este repo verifica sus propias defensas. Los tests se comprueban a sí mismos.

```bash
python3 -m pytest tests/ -q            # baseline antes de tocar nada
```

- Si tocas una defensa, corre su mutación: `python3 scripts/verify_<area>_mutations.py`
- Si tocas esquema, `BYTEA`, cascadas o índices: `python3 scripts/verify_postgres_<area>.py`
  (SQLite no es Postgres en tres cosas que este proyecto usa; ver `docs/testing.md` §3)

**Antes de afirmar que algo funciona, pruébalo.** Un test verde no demuestra que probaste
lo que crees. La forma de saberlo es romper el código a propósito y ver si el test lo nota.
Preferimos un test que muere con un mensaje claro a un `assert len(json) == 1` que
aprueba un 422.

**Al terminar:**

```bash
python3 -m pytest tests/ -q
cd front && npx tsc --noEmit && npm run build
```

Si cambiaste el comportamiento, actualiza en el **mismo commit**: `AGENTS.md`,
`docs/known-issues.md` (§14 se cierra), y `docs/captura.md` si tocaste el pipeline.
Actualiza los conteos de tests en `AGENTS.md` y `docs/testing.md` si cambia el total.

**Al reportar, sé explícito sobre lo que no pudiste verificar.** Si no pudiste levantar
el stack, no digas que lo probaste en vivo. Dime qué quedó sin comprobar.
