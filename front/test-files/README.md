# Probar la digitalización desde el front

`set-digitalizacion` es un enlace a `test-files/set-digitalizacion/` (la raíz del
repo). No es una copia: los binarios los genera un script y dos copias divergen
sin que nadie lo note.

Para regenerar los archivos, desde la raíz del repo:

```bash
docker compose exec expense-api python scripts/generar_set_pruebas_digitalizacion.py
```

## Flujo: elegir carpeta y pulsar

1. Entrar a **Escanear** y elegir una empresa. Sin `company_id` el botón está
   deshabilitado, y con razón: no hay dónde atribuir el gasto.
2. Pulsar **Elegir carpeta** y seleccionar esta carpeta:

   ```
   front/test-files/set-digitalizacion/ideal
   ```

   El navegador junta todos sus archivos (PDF, XML y foto) en una sola selección.
   Debajo del botón se ve `N seleccionados: ...`, con los nombres. Esa lista es
   la confirmación de qué vas a mandar; todavía no se leyó nada.
3. Pulsar **Digitalizar N archivos**. Ahí sí se suben y se leen. La respuesta se
   pinta debajo: cada total va **junto** a su veredicto
   (`datos.extraction_status`), no separado.

Con `ideal` debes ver, entre otros:

| archivo | lector esperado | total |
|---|---|---|
| `01_factura_con_texto.pdf` | `bytes=pdf · lector=texto del PDF` | 1100.00 |
| `02_ticket_con_partidas.pdf` | texto del PDF, IVA cuadra | — |
| `03_cfdi.xml` | `bytes=xml · lector=xml` | — |
| `04_foto_requiere_ocr.jpg` | `OCR local` | — |

Para probar los rechazados, elegir en su lugar la carpeta
`set-digitalizacion/fallido`: `01_no_permitido.docx`, `02_no_permitido.xlsx` y
`03_notas.txt` aparecen **por nombre** en el panel ámbar, no en la tabla de
tickets. La carpeta `borde` tiene casos de extensión que miente y fechas con hora.

También se puede usar **Elegir archivos** si solo quieres mandar uno o dos, por
ejemplo `ideal/01_factura_con_texto.pdf` y
`borde/02_jpg_con_bytes_pdf.jpg` (el nombre dice JPG, los bytes son PDF y se lee
como PDF). El flujo es el mismo: elegir, revisar la lista, pulsar digitalizar.

## Qué NO hace el front

El navegador **no lee el PDF**: lo manda al backend por
`POST /api/v1/expenses/upload-batch` con el campo `files`. El `accept` del
selector sale de `GET /scan/config` y es comodidad; el tipo real lo decide el
`magic` de los bytes en el servidor. Por eso un `.jpg` con bytes de PDF se lee
como PDF y un `.docx` se reporta por nombre.

La otra vía (campo de ruta) es para cuando los documentos ya están en el
**servidor**, no en tu máquina: escribe `/app/test-files/set-digitalizacion` y
pulsa su propio botón. Es independiente de la selección de carpeta del navegador.

## Verificación

```bash
cd front
npm run build        # corre tsc: es la puerta que sí sirve
npm run verify:lote  # 19 comprobaciones sobre el HTML renderizado
```
