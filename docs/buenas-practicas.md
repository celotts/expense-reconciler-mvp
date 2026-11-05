# Buenas prácticas operativas del Expense Reconciler

Lista concisa y viVa: desviarse de esto rompe una defensa que **muere si la quitas**
(o un `verify_*` la detecta). Cada punto remite al documento que lo mide.

## Proceso y gates
1. **Confianza por evidencia, no asunción.** `decide_status()` evalúa los checks
   *antes* de la confianza (`confidence_gate.py:170-188`). 0.99 que no cuadra aritméticamente
   va a `REQUIERE_REVISION`, no a `AUTO_APROBADO`.
2. **No inventar datos.** Total imposible ⇒ `0.00` con motivo, no un número aproximado.
   Subtotal/IVA/IEPS se suman como están impresas; sin subtotal leído no se deriva.
3. **Dos máquinas de estado.** `ExtractionStatus` (cómo se leyó) y `MatchStatus`
   (cómo cuadra con banco); no se confundan. `PERFECT` ≠ aprobado.
4. **Ledger por archivo.** `scan_files` (`relative_path + content_hash + attempts`) para
   saber si se miró algo, no `tickets.source_hash`.
5. **No sobrescribir trabajo humano.** `reviewed_at` bloqua la relectura automática.

## Seguridad y acceso
6. **RBAC por perfil + grupo.** Perfil gobierna el menú; grupo ATWEN lo que se puede
   crear/editar/borrar. `develop`, `qa`, `admin` habilitan el combo de ambientes.
7. **JWT con claims, se re-consulta en BD en cada request.** El cliente no es autoridad.
8. **No formulario público de alta.** Cuentas por script; `X-Contrasena-Actual` para
   acciones sensibles. El último activo no se da de baja.

## Persistencia y evidencia
9. **No editar el comprobante, apilar versiones** (`appended-only`, trigger PostgreSQL);
   `actor` y `motivo` obligatorios desde la segunda.
10. **Informe con una sola verdad.** JSON y PDF salen del mismo objeto;
    `fecha_referencia`, no `emitido_en`, para garantizar bytes deterministas.
11. **Determinismo: mismo periodo → mismo PDF.** Sin `set_creation_date` no hay
    reproducibilidad y no hay evidencia.

## Pruebas
12. **SQLite ≠ PostgreSQL.** Ejecutar `verify_postgres_*.py` dentro del contenedor.
13. **Mutación obligatoria.** Cada defensa toca su `verify_<area>_mutations.py`;
    si el verify no muere al quitársela, la defensa no existe.
14. **Medir, no asumir.** La exactitud viene de `medir_precision_ocr.py`, con
    la verdad rotulada a mano (nunca producida por el OCR).

## Frontend
15. **No calcular en el front.** No sumar ni decidir veredictos en el cierre;
    `exactitud: null` nunca es `0%`.
16. **Token antes de actuar.** `estaVencido(leerToken())` antes de cualquier
    POST/PUT; el 401 muestra el modal de caducidad y regresa al login.

---
Actualizar este archivo junto con `AGENTS.md` cuando cambie una defensa.