# Contrato del producto — Cierre mensual verificable

> **Estado:** Fase 1 **ENTREGADA** (2026-10-06). Fases 2-4 pendientes.
> **Alcance:** define qué se construye, qué se espera y qué se puede afirmar.
> **Complementa** a `AGENTS.md` (contexto operativo) y `docs/known-issues.md` (defectos).
> Este documento define **qué debe ser verdad al terminar**. Los otros dos definen **cómo se trabaja hoy**.

### Fase 1: qué se entregó y qué se apartó

Construido tal como está especificado abajo: `app/services/reporte_cierre.py`,
`app/api/reports.py`, `app/schemas/reporte.py`. Los 10 criterios de §5.3 pasan
(`tests/integration/test_reporte_cierre.py` y `tests/unit/test_reporte_cierre.py`),
y las 7 reglas duras de §5.1 tienen su mutación verificada en
`scripts/verify_reporte_mutations.py`.

**Tres cosas se apartaron de la especificación, y por qué:**

1. **El schema del informe no tiene `emitido_en`.** Tiene `fecha_referencia`,
   derivado del periodo. Sin eso R4 es imposible: `fpdf2` sella `datetime.now()` en
   cada archivo, y dos descargas del mismo periodo no pueden ser byte-idénticas si
   llevan la hora a la que se pidieron. El nombre del campo lo dice, para que nadie
   lo lea como "cuándo lo generó el sistema".
2. **`puede_cerrarse` es un `@computed_field`, no un campo.** Se deriva de
   `pendientes.hay_pendientes`. Como campo aparte se desincronizaba de sus propias
   entradas, y el PDF llegó a imprimir `El periodo NO se puede declarar cerrado: None.`
3. **Hay un cuarto estado de periodo que §5.1 no contemplaba:** `CERRADO_CON_PENDIENTES`.
   `cerrado` (hecho, leído de `cierres_periodo`) y `puede_cerrarse` (lo que R3
   permite afirmar hoy) pueden discrepar cuando alguien escribe en un periodo ya
   cerrado. Reportarlo como `NO_CIERRA` habría sido falso y habría tapado el hallazgo.

**Lo que sigue pendiente y NO es un olvido:** el endpoint que **marca** el cierre.
El informe dice si el periodo puede cerrarse; cerrarlo es una decisión de una persona
y sigue sin endpoint (Fase 4, criterio D1).

---

## 0. Qué es este documento

Un contrato, no una lista de deseos. La diferencia importa: en una lista de deseos se escribe
"el informe debe ser confiable". En un contrato se escribe **qué se mide, con qué umbral, y qué
pasa si no se alcanza**.

Cada línea de este documento cumple uno de estos tres papeles:

| Tipo | Qué hace | Cómo se verifica |
|---|---|---|
| **Regla** | Una restricción que no se puede quitar sin romper algo | Un test que muere si se quita |
| **Criterio** | Una condición observable de "terminado" | Un test que pasa al terminar |
| **Promesa** | Algo que el producto afirma al usuario final | Debe poder medirse en el producto mismo |

**Toda promesa de este documento tiene que ser medible en el producto.** Si no se puede medir,
no es una promesa: es publicidad, y aquí no caben.

---

## 1. Lo que este documento NO promete

Lo primero, porque es lo que más caro sale omitir.

**No prometemos que la IA lea bien.** No hay SLO de "leemos el 97% de los comprobantes". Hay un
SLO de **evidencia**: el sistema afirma, sobreIntervalo de Wilson y no sobre punto medio
(`accuracy_service.py:116-188`), si la evidencia disponible sostiene una afirmación de
exactitud. Hoy ese SLO es 0.96 con nivel de confianza 0.95 y Z=1.96
(`accuracy_service.py:41-58`). **El número es una hipótesis medida, no una garantía.**

**No prometemos automatizar el cierre.** El cierre contable es una decisión profesional firmada
por una persona. Este producto prepara el insumo y deja la firma en manos de quien responde por
los números. Automatizar la firma sería automatizar la responsabilidad, y eso no se hace.

**No prometemos sustituir a un contador.** El producto hace el trabajo mecánico de"",
ordenar, conciliar y documentar. El criterio contable, el tratamiento fiscal y la
responsabilidad profesional siguen siendo de una persona.

**No prometemos que el informe sirva como comprobante fiscal ante el SAT.** Es un documento de
soporte interno y de entrega al cliente. Los requisitos fiscales de un comprobante de retenciones
o de CFDI los emite el SAT y se cumplen con otros sistemas.

**No prometemos un SaaS.** Este producto es local y privado por decisión de diseño
(`README.md:13`, y el razonamiento en `app/models/user.py:14`). Ver §12.

**No prometemos multiusuario simultáneo.** No hay scoping por empresa
(`docs/known-issues.md:73-77`). Mientras eso no exista, el modelo de uso es una máquina y un
contador. La Fase 3 de §4 lo cambia; hasta entonces, el contrato no habla de varios usuarios
al mismo tiempo.

---

## 2. Principios heredados — no negociables

Se heredan de `AGENTS.md` §Reglas. Cualquier trabajo de este contrato los conserva:

1. **La confianza alta nunca compensa un check roto.** `decide_status()` evalúa los checks
   antes que la confianza.
2. **El veredicto se escribe después del `model_dump()`.** `extra='ignore'` **y** orden
   explícito: las dos capas, no una.
3. **Un PDF con texto no toca el modelo.**
4. **El idempotente es `(company_id, source_hash)`**, nunca `source_hash` solo.
5. **El usuario se re-consulta en la BD en cada request.**
6. **El rate limit va antes de gastar scrypt.**
7. **En XLSX solo `forzar_texto`.** El apóstrofo es para CSV.
8. **Allowlist de encodings**, no denylist.
9. **El `content_type` que se sirve no es el que declaró el cliente.**
10. **La aritmética del documento se comprueba antes que RFC y fecha.**

**Regla 11, nueva para este contrato:**
11. **Lo que no se pudo calcular se declara, no se rellena con cero.** Un `0.00` en un informe
    es una afirmación. Un campo ausente no lo es. El proyecto ya aplica esta idea en
    `analitica.py:130-134` (`variacion_pct` devuelve `None` sin base, nunca `Infinity`); este
    contrato la extiende a todo el informe.

---

## 3. El producto final, en una frase

> **Un contador cierra su mes y entrega a su cliente un documento que dice qué se gastó, qué
> leyó el sistema, qué verificó una persona, y —cuando la evidencia no alcanza— lo dice.**

El activo del producto **no es** la IA que lee comprobantes: eso lo hacen todos. El activo es
**la cadena de custodia**:

```
archivo → hash → lectura → confianza → muestra del 5% → veredicto firmado → informe
```

Esa cadena es la que permite responder "¿cómo sabe usted que este gasto es real?" con evidencia
y no con una afirmación. Hoy existe entera salvo el último eslabón: **el papel**.

---

## 4. Alcance por fases

| Fase | Entregable | Esfuerzo | Bloquea a |
|---|---|---|---|
| **0** | `AGENTS.md` y `docs/known-issues.md` al día (§14 marcado cerrado) | 30 min | Toda IA futura |
| **1** | **✅ Informe de cierre mensual exportable** (§5) | hecho | El positioning entero |
| **2** | `VendorNormalizer` en la ruta viva + badge de confianza en la tabla | 1 día | Calidad del dashboard |
| **3** | Despacho / Clientes / scoping por usuario (§7) | 2-3 sem | Retención |
| **4** | Cierre fiscal como entidad + fin de los datos inventados en el export | 2 sem | Firmar el informe |
| — | SaaS multi-tenant | **Fuera de alcance** | Ver §12 |

Las fases son **secuenciales y no se solapan**. La Fase 3 no empieza hasta que la Fase 1 se haya
entregado a un contador real y él haya dicho si lo usaría.

---

## 5. CONTRATO — FASE 1: Informe de cierre mensual

### 5.1 Backend

**Router nuevo:** `app/api/reports.py`, montado en `app/api/api_router.py` como
`reports_router` bajo `/api/v1/reports`.

> **Trampa de orden de rutas** (`docs/known-issues.md` §1): los prefijos literales se declaran
> **antes** de cualquier placeholder. `reports` no colisiona hoy con nada, pero si alguna vez se
> añade `GET /reports/{reporte_id}`, el literal va primero. Ya se rompió una vez.

**Dos endpoints, no uno:**

| Método | Ruta | Devuelve | Para qué |
|---|---|---|---|
| `GET` | `/api/v1/reports/cierre-mensual` | JSON (`ReporteCierreMensual`) | Preview en el frontend |
| `GET` | `/api/v1/reports/cierre-mensual.pdf` | `application/pdf` | Descarga |

Query params en ambos: `company_id: UUID` (obligatorio) y `periodo: str` con forma `YYYY-MM`
(obligatorio, validado con regex `^\d{4}-(0[1-9]|1[0-2])$`).

**Dependencias: ninguna nueva.** El PDF se genera con `fpdf2`, que ya está en
`requirements.txt:29` y hoy solo se usa en `scripts/` y `tests/` para fabricar comprobantes de
prueba. `weasyprint` queda **descartado**: el contenedor es `python:3.11-slim` sin cairo ni
pango (`Dockerfile:1,6-10`) y añadirlo multiplicaría la imagen.

**Servicio nuevo:** `app/services/reporte_cierre.py`, con la lógica. El router es un adaptador
fino. Reusa, **sin duplicar**, lo que ya existe:

| Sección del informe | Origen | Nota |
|---|---|---|
| Comparativo mensual | `analitica.comparativo_mensual` (`analitica.py:474`) | Ya responde *por qué* cambió |
| Hallazgos en prosa | `hallazgos.hallazgos_tendencia` (`hallazgos.py:116`) | Ya devuelve `{tipo,titulo,detalle,tono}` |
| Bloque de exactitud | `accuracy_service.compute_accuracy_report` (`:313`) | **Se reusa tal cual, sin reimplementar** |
| Totales por categoría | `analitica.por_categoria` (`analitica.py:275`) | |

> **Prohibido** escribir una segunda versión del cálculo de exactitud dentro del informe. Si el
> informe dice 94% y el dashboard dice 92%, es un bug, y el test de §5.3-A2 lo detecta.

**Estructura del informe (7 secciones, en este orden):**

1. **Identificación** — empresa (`name`, `tax_id`), periodo, fecha de emisión, quién firma.
2. **Resumen del gasto** — total del periodo, por categoría, comparativo contra el mes anterior
   **cortado a la misma fecha** (`analitica.comparacion_a_la_fecha`, `analitica.py:202`).
3. **Hallazgos** — de `hallazgos.py`, con su tono (`hallazgos.py:61-66`).
4. **Estado de conciliación** — `PERFECT` / `MANUAL` / `DISCREPANCY` / no conciliado.
5. **Bloque de exactitud de la IA** — veredicto global, por origen, intervalos. **El corazón.**
6. **Registro de verificación humana** — cuántas muestras se tomaron, cuántas están firmadas,
   quién (`spot_checked_by`) y cuándo.
7. **Pendientes** — sin categoría (`analitica.pendientes_de_clasificar`, `:516`), sin conciliar,
   discrepancias abiertas.

**Las 7 reglas duras del informe.** Son el contrato de verdad. Cada una muere si se quita:

- **R1.** El informe **nunca** muestra un porcentaje sin su intervalo de confianza al lado.
- **R2.** Si la evidencia no alcanza, eso se dice **en la primera página**, en el bloque 5, no
  como nota al pie ni en gris.
- **R3.** El informe no puede declarar el periodo cerrado si hay tickets sin categoría o sin
  conciliar, salvo que el contador lo marque explícitamente (y entonces el informe registra que
  lo fue).
- **R4.** **Determinismo:** mismo `periodo` + misma base de datos → **PDF byte-idéntico**.
  `fpdf2` sella fecha de creación: hay que fijarla a un valor derivado del periodo, no a
  `datetime.now()`.
- **R5.** "Leído por el sistema" y "verificado por una persona" **nunca** llevan la misma
  etiqueta. Son afirmaciones de distinta fuerza.
- **R6.** El informe **no inventa** datos ausentes. Mientras la Fase 4 no arranque,
  `TipoCambio` se declara como pendiente — hoy el export manda `"1.0000"` fijo
  (`export_service.py:310-327`) y un comprobante en USD sale sin marca de error.
- **R7.** Una sección que no se pudo calcular dice **"no disponible"**. No se omite y no se pone
  en cero (Principio 11).

### 5.2 Frontend

**Página nueva** `front/src/pages/Cierre.tsx`, ruta `/cierre`, entrada en el sidebar
(`App.tsx`), y enlace desde `Dashboard.tsx`.

| Elemento | Requisito |
|---|---|
| Selector de periodo | Mes y año; por omisión, el mes en curso |
| Preview | Las 7 secciones, en HTML, con los mismos datos que el PDF |
| Banner de estado | **Visible arriba del todo** si el informe no cumple R2 o R3 |
| Botón de descarga | `application/pdf`, nombre `cierre_<tax_id>_<periodo>.pdf` |
| Enlace a la evidencia | Desde el bloque 5, a la página de muestreo que ya existe |

**Restricción de UI, y es la importante:** el preview y el PDF se generan del **mismo objeto
serializado**. Si el front tiene su propia lógica de presentación de la exactitud, el contador
verá dos números distintos. Reutilizar `utils/veredicto.ts` y los componentes de
`SpotCheck.tsx`; no reescribir la traducción de estados.

### 5.3 Criterios de aceptación

Cada criterio es un test. La fase no está terminada si alguno falla.

| # | Criterio | Test |
|---|---|---|
| A1 | El PDF se descarga con 200, `Content-Type: application/pdf` y abre sin error | `tests/integration/test_reporte_cierre.py` |
| A2 | El total del informe **iguala** una suma independiente de `total_amount` por `expense_date` del periodo, calculada en el test | idem |
| A3 | Con el muestreo vacío, el bloque 5 dice `SIN_EVIDENCIA` — **no** `0%` ni `96%` | idem |
| A4 | Dos llamadas del mismo periodo producen **bytes idénticos** (R4) | idem |
| A5 | Un `periodo` con regex inválido devuelve 422 | idem |
| A6 | Ninguna ruta del cálculo usa `float`. Grep que falle si aparece | `tests/unit/test_reporte_cierre.py` |
| A7 | Reusa `compute_accuracy_report`: si el servicio cambia, el informe cambia | test que parchea el servicio y comprueba que el informe refleja el valor |
| A8 | Un ticket sin categoría aparece en la sección 7 y bloquea el "cerrado" (R3) | `tests/integration/test_reporte_cierre.py` |
| A9 | La API devuelve 401 sin token y 404 con `company_id` inexistente | idem |
| A10 | El archivo del PDF **no** lleva rutas absolutas del servidor ni el `SECRET_KEY` | `tests/unit/test_reporte_cierre.py` |

---

## 6. CONTRATO — FASE 2: Saneamiento

| # | Trabajo | Criterio de aceptación |
|---|---|---|
| B1 | `VendorNormalizer.normalize()` en la ruta viva de captura (`capture.py`) | "Oxxo", "OXXO" y "OXXO EXPRESS" colapsan a **un** proveedor en el dashboard. Test con las tres formas. |
| B2 | Badge de `confidence` + `extraction_status` en la tabla de tickets (`Tickets.tsx:408-435`) | Un ticket `AUTO_APROBADO` y uno `MANUAL` **se distinguen a simple vista** en la lista |
| B3 | Actualizar `AGENTS.md` y `known-issues.md` | Ninguna afirmación del repo describe un estado que el código no pueda alcanzar |

**Sobre B2 y las deps de embeddings:** antes de tocar `torch`/`sentence-transformers`
(`requirements.txt:16-18`) se mide RAM con `make stats` y se registra el antes y el después.
`create_embedding()` no tiene callers y la extensión `vector` (`db/init.sql:3`) no tiene ni una
columna `vector` — es andamiaje. **Pero** el import de `torch` está encadenado a la cadena de
vivos de la API (`main.py:4 → api_router.py:11 → tickets.py:38 → ai_extractor.py:12 →
ai_client.py:9`), así que quitarlo no es "limpiar imports": es tocar la ruta de la IA. Con
medición, no de palabra.

---

## 7. CONTRATO — FASE 3: Despacho y Clientes

> **No se empieza hasta que la Fase 1 se haya entregado a un contador real.** Si en la Fase 1
> nadie dice "sí, esto se lo entrego a mi cliente", la Fase 3 no tiene justificación y el
> proyecto se queda en un generador de informes.

**El concepto que falta:** hoy `users` **no** tiene `company_id` y `companies` **no** tiene
`owner_id` (`app/models/user.py:25-31`, `app/models/company.py:13-16`). Un contador abre la
aplicación y ve **una lista global de empresas**. No tiene "mis clientes".

```
Despacho (el contador)  →  Clientes (empresas)  →  Cierres mensuales  →  Informes entregados
```

| # | Trabajo | Criterio de aceptación |
|---|---|---|
| C1 | Relación usuario↔empresa | Un usuario solo ve y toca sus empresas. Con token válido, `GET /tickets?company_id=<ajena>` → 404, no una lista ajena |
| C2 | Selector global de empresa | Hoy cada pantalla lleva su propio `<select>`; en Fase 1 vive en la URL. No es un defecto, es un costo operativo: 6 selectores que se re-eligen en cada vista |
| C3 | Selector de empresa en el Dashboard | Hoy `Dashboard.tsx` no tiene selector, y `analitica._filtro_empresa:244-245` devuelve `[]` sin `company_id`: **el tablero sin empresa mezcla todas** |
| C4 | Empresa: régimen fiscal, código postal, domicilio, fecha de inicio de operaciones | Hoy `Company` tiene 4 columnas (`app/models/company.py:10-16`) y el backend acepta **cualquier string** como RFC (`schemas/company.py:9`, solo `min_length=1`). Se valida en el front y no en el servidor |

> **C1 es también la parte de seguridad de `docs/known-issues.md` §4.** La diferencia es el
> propósito: aquí no es "evitar que alguien lea empresas ajenas", es "que el contador organice
> sus clientes". El trabajo técnico es el mismo; el criterio de aceptación es otro.

---

## 8. CONTRATO — FASE 4: Cierre fiscal

Hoy no existe el concepto. Cero: sin periodo, sin corte, sin ejercicio, sin congelamiento
(`grep -i 'cierre|fiscal|ejercicio'` solo encuentra comentarios).

| # | Trabajo | Criterio de aceptación |
|---|---|---|
| D1 | `PeriodoContable` (empresa, ejercicio, mes) con estados `ABIERTO` / `CONCILIADO` / `CERRADO` | Un periodo cerrado **rechaza** nuevas escrituras con 409 |
| D2 | Congelamiento | Un ticket cuyo `expense_date` cae en un periodo `CERRADO` no se puede modificar sin una reapertura explícita y auditada |
| D3 | Fin de los datos inventados en el export | `export_service.py:310-327` deja de mandar `TipoCambio="1.0000"`, `Moneda="MXN"`, `MetodoPago="PUE"`, `Serie=""`, `Folio=""` fijos. Un comprobante en USD sale **con el tipo de cambio real o con la celda vacía y una marca de error** |
| D4 | Los 12 campos que se extraen y se tiran | `invoice_number`, `invoice_series`, `currency`, `exchange_rate`, `payment_method`, `items`, `provider_address`, `receiver_*`, `due_date`, `tax_breakdown`, `payment_terms` llegan al informe o se dejan de pedir al modelo. **Extraer y descartar es peor que no extraer** |
| D5 | Un periodo cerrado genera su informe y ese informe es inmutable | Reabrirlo deja rastro |

---

## 9. Definiciones de confiabilidad y precisión

Cuatrocepts distintos que se confunden. Este producto los separa porque cada uno falla distinto.

### 9.1 Precisión numérica
Los importes no se mueven. `Decimal` en todo el camino, nunca `float` (`AGENTS.md` §Convenciones).
El redondeo a 2 decimales ocurre **una vez**, en el borde de salida, y se declara en el
documento. Criterio: A6.

### 9.2 Precisión analítica
El sistema **no afirma lo que no puede sostener.** Esto ya está construido y es lo que más se
distingue de la categoría:

- El veredicto de exactitud se toma sobre el **intervalo de Wilson entero**, no sobre el punto
  medio (`accuracy_service.py:167-188`).
- El veredicto global es el **peor** de los orígenes, nunca el promedio (`tickets.py:857-873`).
- Los tres orígenes (`llm`, `pdf_text`, `rules`) se reportan **aunque estén vacíos**
  (`accuracy_service.py:58`), para que un vacío no se lea como un acierto.
- `MINIMO_MESES=4` (`hallazgos.py:59`): con menos datos, `datos_insuficientes` en vez de un
  veredicto sobre nada.
- `base_atipica` usa umbral 0.25 distinto de `atipico` 0.50 (`hallazgos.py:50,56`), porque la
  comparación contra una base anómala no es una comparación.

### 9.3 Precisión legal
El informe es un documento que alguien firma. Eso obliga a:

- Cada afirmación del informe es trazable a un registro: un número del informe se deriva de una
  consulta identificable, no de un cálculo en el aire.
- `spot_checked_by` es el correo del token, nunca una constante (`test_audit_trail.py`).
- El informe **no afirma** ser comprobante fiscal (§1).
- Mientras la Fase 4 no exista, el periodo **no se declara cerrado** (R3).

### 9.4 Confiabilidad del sistema
| Métrica | Hoy | Objetivo | Cómo se mide |
|---|---|---|---|
| Tests | 1380 | ≥ 1380, cero regresión | `pytest tests/ -q` |
| Verificaciones por mutación | 9 áreas | 9 áreas, sin regresión | `scripts/verify_*_mutations.py` |
| Exactitud declarada | SLO 0.96, **medido** | Se reporta, no se promete | `GET /tickets/accuracy` |
| Muestreo | 5% determinístico por hash (`enums.py:209`) | Igual | Contenido, no aleatorio |
| Determinismo del informe | **byte-idéntico (R4, verificado)** | Igual | `TestA4Determinismo` |
| Cero datos inventados | 0 vecindarios rotos | 0 | `TestElDocumentoNoSeInventaDatos`, criterio D3 |

### 9.5 Lo que el producto afirma, y lo que no

| El producto SÍ dice | El producto NO dice |
|---|---|
| "El 5% de las lecturas automáticas se verificó a mano" | "La IA lee bien el 96% de los comprobantes" |
| "El intervalo de confianza de esta vía es [0.74, 0.99]" | "La exactitud es 96%" |
| "No hay evidencia suficiente para afirmar el SLO" | "La exactitud es 0%" |
| "Estos N pendientes requieren tu criterio" | — |
| "Un humano firmó esta muestra el día tal" | — |
| "El tipo de cambio no está disponible" | "El tipo de cambio es 1.0000" |

**La columna derecha es la que este producto se niega a tener.** Cada vez que alguien quiera
rellenarla, la respuesta es la regla 11.

---

## 10. Reglas de no-regresión

Ninguna fase se entrega sin esto:

```bash
python3 -m pytest tests/ -q                       # 1380+, cero rojos
python3 scripts/verify_capture_mutations.py       # si se tocó captura
python3 scripts/verify_export_mutations.py        # si se tocó export/texto
python3 scripts/verify_auth_mutations.py          # si se tocó auth o veredictos
python3 scripts/verify_reconciliation_mutations.py
python3 scripts/verify_spot_check_mutations.py
```

Y si el cambio toca esquema, `BYTEA`, cascadas o índices, se corre además la verificación
contra **Postgres real** (`docs/testing.md`): SQLite no es Postgres en FKs, en `BYTEA` de 12 MB
ni en índices `postgresql_where`, y un test verde sobre SQLite no dice nada de esas tres cosas.

**Cada defensa nueva nace con su mutación.** Un test que pasa con y sin la defensa no prueba
nada; el valor de un test está en que **muera**.

---

## 11. Degradación: qué pasa cuando algo falla

Un producto contable que se cae a mitad de un cierre es peor que uno que se niega a empezar.
Cada fallo tiene un comportamiento declarado:

| Fallo | Comportamiento | Por qué |
|---|---|---|
| El modelo no está disponible | El informe se genera igual, con las lecturas del periodo marcadas como no verificables. **No se inventan.** | Un informe incompleto con una marca vale más que uno completo y falso |
| Un ticket no tiene documento | Se reporta como pendiente de original | Sin comprobante no hay cadena de custodia |
| La consulta de exactitud falla | La sección 5 dice **"no disponible"** (R7). **Nunca `0%`.** | Principio 11 |
| El periodo no existe | 404 con mensaje explícito, no un informe vacío | Un vacío silencioso se lee como "no gastaste" |
| El PDF falla a media generación | Se descarta entero. Nunca un PDF truncado | Un PDF parcial es un documento falsificado |

---

## 12. Fuera de alcance

Explícitamente, para que no se cuele por.Tests de cómo:

- **SaaS multi-tenant.** En SaaS hay que compartir un modelo barato entre muchos tenants, y el
  valor de este producto está en lo contrario: **alto coste por cliente, pocos clientes, alto
  valor** (modelo dedicado, muestreo del 5%, firma humana). El SaaS de volumen obligaría a bajar
  el modelo y el muestreo — es decir, a destruir el producto. Si algún día se hace, es como
  *opción para* un contador grande, no como el producto.
- **Enviar comprobantes a un proveedor externo.** La confidencialidad del cliente del contador
  es el argumento de venta, no una limitación.
- **Emitir CFDI, retenciones o cualquier comprobante del SAT.**
- **Facturación y cobros.**
- **App móvil nativa.** La web responsive cubre el caso; la cámara ya está
  (`components/ui/CameraCapture.tsx`).
- **Multi-moneda y tipo de cambio.** Se declara pendiente, no se estima (D3).
- **Cuente de iCloud / Drive para los comprobantes.** Los bytes viven en `BYTEA`
  (`db/init.sql:199`); en local es correcto.

---

## 13. Definition of Done por fase

Una fase está terminada cuando **todo** esto es cierto:

1. Los criterios de aceptación de su sección (§5.3, §6, §7, §8) pasan.
2. `pytest tests/ -q` en verde, **sin** tests borrados ni saltados.
3. Las verificaciones por mutación de las áreas tocadas, en verde.
4. Si tocó esquema: migración numerada nueva + `verify_postgres_*.py` en verde.
5. `AGENTS.md` y `docs/known-issues.md` actualizados **en el mismo commit** que el código.
   La documentación que no se actualiza con el código es peor que no tenerla.
6. El README deja de afirmar algo que el código ya no puede cumplir.
7. Una nota de qué **no** se hizo y por qué.

---

## 14. Riesgos aceptados

Se aceptan **a conciencia**, con la consequence escrita:

| Riesgo | Se acepta porque | Qué pasa si ocurre |
|---|---|---|
| El contador mexicano no paga por esto | Es el riesgo de mercado número uno y ningún código lo arregla | Se valida en la Fase 1, antes de la 3. Cinco entrevistas valen más que 8 semanas |
| El diferenciador es invisible para el comprador medio | "Digo honestamente cuándo no sé" no es un argumento de venta, es una garantía | Se traduce a lenguaje de riesgo: lo que el contador **tiene que poder defender** |
| La Fase 3 es la más cara y la más fácil de posponer | Si se pospone, el proyecto se queda en un generador de informes | Se pospone a propósito hasta que haya evidencia de uso |
| El informe es nuevo y no tiene precedente al que compararse | No hay a quién preguntar qué espera un contador de esto | Por eso el primer entregable es el **documento**, no la función: se enseña y se pregunta |
| Quitar `torch` puede romper la cadena de imports de la IA | El import está encadenado a 5 archivos | Fase 2 exige medición con `make stats` antes y después |

---

## 15. La decisión que este contrato no toma

**¿La Fase 1 le resuelve un problema a alguien, o solo demuestra que se puede hacer?**

**Esta es ahora la única decisión abierta, y ya no se responde leyendo el código.**
El documento existe y cumple las reglas duras. Lo que sigue es enseñárselo a cinco
contadores y preguntarles si se lo entregarían a un cliente.

Esa conversación decide si existe la Fase 3. **Ninguna cantidad de trabajo en la
Fase 3 sustituye esa respuesta, y empezar la Fase 3 sin ella es la forma más cara de
no decidir.**

