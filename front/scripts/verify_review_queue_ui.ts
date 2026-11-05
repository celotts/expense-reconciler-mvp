/**
 * Render de la cola de revision contra la API real, sin navegador.
 *
 * Que no haya un runner de tests en el front no es motivo para no verificar la
 * pantalla. Los tests de Python corren contra SQLite y no ven nada del
 * render; aqui se cubre la otra mitad: que el payload que devuelve la API se
 * convierta en texto que un humano pueda usar.
 *
 * Se renderiza a HTML con react-dom/server porque lo que importa verificar es
 * el TEXTO que sale, no los pixeles. Si un estado no tiene etiqueta, si un
 * motivo no se traduce o si un campo llega en null inesperado, aqui se ve.
 *
 * Uso, desde front/:
 *     npx esbuild scripts/verify_review_queue_ui.ts --bundle --platform=node \
 *         --format=cjs --outfile=/tmp/rq.cjs --log-level=error \
 *         && node /tmp/rq.cjs
 *
 * Requiere la API levantada y con la cola sembrada.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { Resumen, TicketEnCola, EditorTicket, DialogoDescartar } from '../src/pages/ReviewQueue';
import type { Ticket, TicketReviewQueue, ExtractionStatus } from '../src/types/api';

const API = process.env.API_URL || 'http://localhost:8000/api/v1';

let fallos = 0;
function check(desc: string, cond: boolean) {
  console.log(`  ${cond ? 'OK   ' : 'FALLA'} ${desc}`);
  if (!cond) fallos++;
}

/** Texto visible de un HTML: le quita tags y colapsa espacios. */
function texto(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Etiquetas de los inputs, para ver que campos ofrece el editor. */
function inputsDe(html: string): string[] {
  return [...html.matchAll(/<(?:input|textarea)[^>]*>/g)]
    .map(m => (m[0].match(/value="([^"]*)"/) || [, ''])[1])
    .filter(Boolean);
}

const noop = () => undefined;

async function main() {
  console.log(`Leyendo la cola real de ${API}...`);
  const res = await fetch(`${API}/tickets/review-queue?limit=100`);
  if (!res.ok) throw new Error(`la API devolvio ${res.status}`);
  const cola: TicketReviewQueue = await res.json();

  console.log(`\n0) La API si tiene datos que renderizar (${cola.total_open} en cola)`);

  // Sin datos, el resto del script sigue y revienta con
  // `Cannot read properties of undefined (reading 'validation_errors')` en la
  // primera tarjeta. Ese error no dice nada util: parece un problema del render
  // cuando lo que pasa es que no hay nada que renderizar. Y un verificador que
  // revienta por un estado normal de la base es un verificador que nadie
  // ejecuta, porque la base vacia es el estado en que esta casi siempre.
  //
  // Aqui no se siembra. Sembrar para poder verificar deja datos de prueba en
  // la base, y un verificador que acumula lo que verifica deja de poder
  // verificar. Lo que se hace es DECIR que la base esta vacia y salir con
  // codigo 0: la pantalla se revisa sembrando a mano cuando hace falta.
  //
  // NO se cuenta como fallo. Imprimir "FALLA" y salir con 0 seria peor que
  // reventar: alguien lo lee como un problema del render y va a buscar un bug
  // que no esta. Aqui no hay nada que revisar todavia, y eso se dice.
  if (cola.tickets.length === 0) {
    console.log('\n  SIN DATOS: la base no tiene tickets en cola. La parte de');
    console.log('  render con datos reales no se corro, y eso no es un fallo.');
    console.log('  Para verificar el texto: siembra la cola a mano y vuelve a lanzar.');
    process.exit(0);
  }
  check('la cola no vino vacia', cola.tickets.length > 0);

  // -----------------------------------------------------------------------
  console.log('\n1) Resumen: los numeros que salen son los de la API');
  const htmlResumen = renderToStaticMarkup(
    <Resumen
      totalAbiertos={cola.total_open}
      porEstado={cola.por_estado}
      antiguedad={cola.antiguedad_promedio_dias}
      onFiltrar={noop}
      filtroActivo=""
    />,
  );
  const tResumen = texto(htmlResumen);
  check(`muestra el total en cola (${cola.total_open})`, tResumen.includes(String(cola.total_open)));
  for (const [estado, n] of Object.entries(cola.por_estado)) {
    if (n) check(`  cuenta ${estado} = ${n}`, tResumen.includes(String(n)));
  }
  check('cada tarjeta dice de donde viene el ticket',
    cola.tickets.every((t) => {
      const h = renderToStaticMarkup(
        <TicketEnCola ticket={t} onRefresh={noop} onEditar={noop}
          onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
      );
      return texto(h).includes('Origen:');
    }));

  // -----------------------------------------------------------------------
  console.log('\n2) Cada ticket se renderiza sin reventar');
  let raws: string[] = [];
  for (const t of cola.tickets) {
    let html = '';
    try {
      html = renderToStaticMarkup(
        <TicketEnCola
          ticket={t}
          onRefresh={noop}
          onEditar={noop}
          onDescartar={noop}
          editando={false}
          onCancelarEdicion={noop}
        />,
      );
      check(`  ${t.extraction_status} ${t.source_file ?? t.provider_name}`, html.length > 100);
    } catch (e) {
      check(`  ${t.extraction_status} ${t.source_file ?? t.provider_name}`, false);
      console.log(`        ${(e as Error).message}`);
    }
    // Codigos crudos que se hayan colado en pantalla: son la falla de este
    // archivo. Un revisor no puede corregir `provider_missing`.
    for (const c of [
      'provider_missing', 'total_not_positive', 'tax_negative', 'tax_exceeds_total',
      'subtotal_plus_tax_mismatch', 'malformed_rfc', 'date_missing', 'date_in_future', 'date_too_old',
    ]) {
      if (texto(html).includes(c)) raws.push(`${t.source_file ?? t.id}: ${c}`);
    }
  }
  check(`ningun codigo crudo visible (${raws.length ? raws.join(', ') : 'limpio'})`, raws.length === 0);

  // -----------------------------------------------------------------------
  console.log('\n3) El motivo se explica en espanol, con los numeros');
  const conMismatch = cola.tickets.find(t => (t.validation_errors ?? '').includes('subtotal_plus_tax'));
  if (conMismatch) {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={conMismatch} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    const m = (conMismatch.validation_errors ?? '').match(/leido=([\d.]+),esperado=([\d.]+)/);
    check('menciona el total leido', !!m && t.includes(m![1]));
    check('menciona el total esperado', !!m && t.includes(m![2]));
    check('dice que no cuadra', t.toLowerCase().includes('no cuadra'));
  } else {
    check('habia un caso de desglose aritmetico', false);
  }

  const conFecha = cola.tickets.find(t => (t.validation_errors ?? '').includes('date_'));
  if (conFecha) {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={conFecha} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    const f = (conFecha.validation_errors ?? '').match(/\(([^)]+)\)/)?.[1] ?? '';
    check(`menciona la fecha leida (${f})`, !!f && t.includes(f));
    check('dice que esta en el futuro o muy vieja',
      t.includes('futuro') || t.includes('mas de 3'));
  }

  // -----------------------------------------------------------------------
  console.log('\n4) "Unknown Provider" no se muestra como si fuera un nombre');
  const sinProv = cola.tickets.find(t => t.provider_name === 'Unknown Provider');
  if (sinProv) {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={sinProv} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    check('avisa que no se identifico el proveedor', t.includes('Sin proveedor identificado'));
    check('no lo presenta como proveedor leido', !t.includes('Unknown Provider'));
  } else {
    check('habia un ticket sin proveedor', false);
  }

  // -----------------------------------------------------------------------
  console.log('\n5) La confianza manual no se muestra como 0%');
  const manual: Ticket = {
    ...(cola.tickets[0] as Ticket),
    confidence: null,
    confidence_source: 'manual',
    extraction_status: 'PENDIENTE' as ExtractionStatus,
  };
  const tManual = texto(renderToStaticMarkup(
    <TicketEnCola ticket={manual} onRefresh={noop} onEditar={noop}
      onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
  ));
  check('dice Manual', tManual.includes('Manual'));
  check('no dice 0%', !tManual.includes('Confianza: 0'));

  const conConf = cola.tickets.find(t => t.confidence);
  if (conConf) {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={conConf} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    check(`muestra la confianza en % (${conConf.confidence})`,
      t.includes(`${(Number(conConf.confidence) * 100).toFixed(1)}%`));
  }

  // -----------------------------------------------------------------------
  console.log('\n6) Acciones disponibles en cada ticket de la cola');
  const conAcciones = texto(renderToStaticMarkup(
    <TicketEnCola ticket={cola.tickets[0]} onRefresh={noop} onEditar={noop}
      onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
  ));
  check('boton Corregir', conAcciones.includes('Corregir'));
  check('boton Aprobar', conAcciones.includes('Aprobar'));
  check('boton Descartar', conAcciones.includes('Descartar'));

  // -----------------------------------------------------------------------
  console.log('\n7) El editor ofrece corregir los campos que el gate revisa');
  const editable: Ticket = {
    ...(cola.tickets[0] as Ticket),
    provider_name: 'Unknown Provider',
    total_amount: '0.00',
    tax_amount: '0.00',
    expense_date: '2026-09-20',
  };
  const htmlEditor = renderToStaticMarkup(
    <EditorTicket
      ticket={editable}
      ocupado={false}
      onCancelar={noop}
      onAprobar={noop}
    />,
  );
  const vals = inputsDe(htmlEditor);
  check('el campo proveedor arranca vacio (no "Unknown Provider")',
    !vals.includes('Unknown Provider'));
  check('trae el total actual', vals.includes('0.00'));
  check('trae la fecha actual', vals.includes('2026-09-20'));
  check('advierte que el sistema vuelve a validar', texto(htmlEditor).includes('vuelve a validar'));
  check('el boton dice que aprueba y guarda', texto(htmlEditor).includes('Aprobar y guardar'));

  // -----------------------------------------------------------------------
  console.log('\n8) Descartar pide un motivo');
  const htmlDlg = renderToStaticMarkup(
    <DialogoDescartar ticket={cola.tickets[0]} onCerrar={noop} onHecho={noop} />,
  );
  const tDlg = texto(htmlDlg);
  check('pide el motivo', tDlg.includes('Motivo'));
  check('dice que no se borra', tDlg.toLowerCase().includes('no se borra'));
  check('dice que no entra a conciliacion', tDlg.includes('no entra a conciliación'));
  check('el dialogo nombra el archivo que se descarta',
    cola.tickets[0].source_file ? tDlg.includes(cola.tickets[0].source_file) : true);

  // -----------------------------------------------------------------------
  console.log('\n9) Adversarial: un estado que la pantalla no conoce');
  // Si el backend agrega un estado y esta pantalla no lo tiene, la cola
  // revienta entera y queda en blanco. Una cola en blanco no da error: da la
  // impression de que no hay nada pendiente, que es la mentira mas cara que
  // puede contar esta pantalla.
  const raro = { ...(cola.tickets[0] as Ticket), extraction_status: 'ESTADO_DEL_FUTURO' as ExtractionStatus };
  try {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={raro} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    check('no revienta', t.length > 50);
    check('igual muestra el estado crudo para poder reportarlo',
      t.includes('ESTADO_DEL_FUTURO'));
  } catch (e) {
    check('no revienta con un estado desconocido', false);
    console.log(`        ${(e as Error).message}`);
  }

  // -----------------------------------------------------------------------
  console.log('\n10) Adversarial: campos que llegan en null');
  const vacio: Ticket = {
    ...(cola.tickets[0] as Ticket),
    provider_name: '',
    provider_tax_id: null,
    category: null,
    source_file: null,
    source_type: null,
    raw_text: null,
    confidence: null,
    confidence_source: null,
    validation_errors: null,
  };
  try {
    const t = texto(renderToStaticMarkup(
      <TicketEnCola ticket={vacio} onRefresh={noop} onEditar={noop}
        onDescartar={noop} editando={false} onCancelarEdicion={noop} />,
    ));
    check('no revienta con todo en null', t.length > 50);
    check('dice que no se identifico el proveedor', t.includes('Sin proveedor identificado'));
    check('explica que no hay problemas registrados en vez de callarse',
      t.includes('Sin problemas registrados'));
  } catch (e) {
    check('no revienta con todo en null', false);
    console.log(`        ${(e as Error).message}`);
  }

  console.log(`\n${fallos ? `FALLARON ${fallos}` : 'TODO OK'}`);
  process.exit(fallos ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
