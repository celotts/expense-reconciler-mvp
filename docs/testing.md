# Estrategia de pruebas

Este proyecto tiene algo particular: **los tests se verifican a sí mismos**.
No es una formalidad. Es la razón de que varios tests de seguridad existan en vez de ser
opcionales.

```bash
python3 -m pytest tests/ -q          # 642  (440 unit + 202 integration)
```

---

## 1. Por qué un test verde no demuestra nada

Un test puede pasar por dos razones: porque el código hace lo que debe, o porque el test
no está probando lo que crees. Desde fuera no se distinguen.

La única forma de saber cuál de las dos es **romper el código a propósito** y ver si el
test se da cuenta. Eso es la verificación por mutación.

**Ejemplo real de este repo:** una mutación cambió `date.today()` por `utcnow().date()`.
Es un cambio de una palabra, no se nota en la mayoría de las revisiones, y hace que un
gasto quede fechado mañana. Los tests de fecha de negocio lo detectaron.

Si una mutación **sobrevive**, significa que ningún test cubre esa pieza, y el código
puede volver a romperse sin que nadie se entere.

---

## 2. Verificación por mutación

Cada defensa de seguridad tiene al menos una mutación que la mata. Correrlas es barato
y es la única señal real de que la defensa está vigilada.

| Script | Mutaciones | Área |
|---|---|---|
| `verify_capture_mutations.py` | 26 | Ruta de captura, idempotencia, content-type |
| `verify_auth_mutations.py` | 37 | Auth, rate limit, forja de veredictos |
| `verify_spot_check_mutations.py` | 20 | Muestreo y evidencia de exactitud |
| `verify_reconciliation_mutations.py` | 18 | Matching engine |
| **Total** | **101** | |

```bash
python3 scripts/verify_capture_mutations.py     # salida 0 = toda mutación muere
```

**Cómo funciona:** cada mutación es una tupla `(nombre, archivo, texto_original,
texto_mutado, tests_que_deben_morir)`. El script copia el repo a un temp, aplica el
reemplazo textual, corre los tests indicados y verifica que **fallen**. Si pasan, la
mutación sobrevivió y el script sale con 1.

**Regla:** si tocas una defensa, corre su verificador. Si **agregas** una defensa,
agrega su mutación. Una defensa sin mutación no está vigilada, por mucho que tenga tests.

---

## 3. SQLite no es Postgres — y por qué importa

`tests/conftest.py:20` usa `sqlite+aiosqlite:///:memory:`. SQLite diverge de Postgres
en tres cosas que este proyecto sí usa:

| Diferencia | Consecuencia en un test verde |
|---|---|
| **Ignora las FKs por omisión** | Un `ON DELETE CASCADE` no se ejecuta. Un test puede afirmar que el borrado en cascada funciona sin haberlo probado. |
| **Acepta un `BYTEA` de 12 MB sin quejarse** | Postgres sí lo rechaza (TOAST). El test pasa con un tamaño que en producción es un `413`. |
| **Ignora los índices `postgresql_where`** | Los índices parciales (`WHERE source_hash IS NOT NULL`, los de las colas) no se ejercitan. |

Además: **SQLite construye el esquema desde los modelos ORM**, no desde `db/init.sql`.
Si divergen, el test valida un esquema que no es el de producción. Ya hay un caso
(`docs/known-issues.md` §9): `bank_transactions.company_id` es nullable en el SQL y
`NOT NULL` en el modelo.

**Regla:** si tu cambio toca esquema, `BYTEA`, cascadas o índices parciales, el test
unitario **no basta**. Corre el script de Postgres correspondiente.

### Verificación contra Postgres real

```bash
python3 scripts/verify_postgres_capture.py         # idempotencia, tope de raw_text
python3 scripts/verify_postgres_documentos.py      # bytes intactos, CASCADE, UNIQUE
python3 scripts/verify_postgres_auth.py            # índice lower(email), unique
python3 scripts/verify_postgres_gate.py            # constraints de tickets
python3 scripts/verify_postgres_reconciliation.py  # matching sobre datos reales
python3 scripts/verify_postgres_spot_check.py      # muestreo y evidencia
```

Estos usan la base **migrada de verdad** y el código real de la API. Requieren el stack
levantado (`make up`). No usan IA: la ruta que verifican es la determinista.

---

## 4. El precedente que justifica todo esto

`tests/integration/test_reconciliations_api.py:420` afirma:

```python
assert len(list_resp.json()) == 1
```

El endpoint está **roto**: `GET /{reconciliation_id}` se declara antes que `/mappings` en
`reconciliations.py`, así que Starlette matchea primero y devuelve **422** con un
`{"detail": [...]}`. Ese dict también cumple `len == 1`.

**El test pasa en verde sobre un endpoint que devuelve error.** Está documentado en
`docs/known-issues.md` §1.

La lección generaliza: **afirmar sobre la forma de la respuesta puede darle por bueno a
un error.** Un test de API debe exigir el código de estado explícitamente:

```python
assert resp.status_code == 200          # sí
assert len(resp.json()) == 1            # no: un 422 también cumple
```

---

## 5. Qué debe tener test

| Cambio | Test mínimo | Además |
|---|---|---|
| Regla del confidence gate | unit que muera al cambiar el umbral o el orden | mutación |
| Defensa de seguridad (fórmula, codec, content-type, rate limit) | test de ataque con payload real | mutación |
| Esquema, constraints o índices | unit | script de Postgres |
| Endpoint nuevo | integration que exija **status explícito** | — |
| Export | round-trip: abrir el archivo generado y verificar que **no** hay `data_type == "f"` | mutación |
| Rango de un enum | test de que el valor está / no está | mutación si tiene defensa |

**Criterio del export de inyección** (`test_export_injection.py:25-30`): no comprueba
que se haya saneado, comprueba que **no exista ninguna celda con `data_type == "f"`**
en el XLSX generado. Da igual el método; lo único prohibido es el resultado. Y prueba
también los 6 valores que el filtro **no** debe tocar (`"OXXO"`, `"a-b"`, `"Ho"`…): un
filtro que sana de más está rompiendo datos de verdad.

---

## 6. Antes de dar por terminado

```bash
python3 -m pytest tests/ -q
# si tocaste una defensa:
python3 scripts/verify_<area>_mutations.py
# si tocaste esquema, BYTEA, cascadas o índices:
python3 scripts/verify_postgres_<area>.py
```

Si un test **no falla** cuando debería, el problema es el test, no el código.
