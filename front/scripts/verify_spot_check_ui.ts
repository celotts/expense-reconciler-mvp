/**
 * Render del muestreo contra la API real, sin navegador.
 *
 * Que no haya un runner de tests en el front no es motivo para no verificar la
 * pantalla. Los tests de Python corren contra SQLite y no ven nada del render;
 * aqui se cubre la otra mitad: que el payload que devuelve la API se convierta
 * en texto que un humano pueda usar, y que no afirme nada que los datos no
 * sostengan.
 *
 * El render se hace a HTML con react-dom/server porque lo que importa verificar
 * es el TEXTO que sale, no los pixeles. Si un veredicto no tiene etiqueta, si
 * un `null` se pinta como `0%` o si un campo llega en null inesperado, aqui se
 * ve.
 *
 * Por que NO se siembra la base: sembrar para poder renderizar deja datos de
 * prueba en la base, y un verificador que acumula lo que verifica deja de
 * poder verificar. Ademas, para encontrar los fallos que importan (un `null`
 * pintado como 0, un veredicto desconocido, una via sin datos) hace falta
 * exactamente lo contrario: un caso que la base real NO produce. Por eso la
 * forma del payload se contrasta contra la API viva, y el render se hace con
 * los tres casos que de verdad rompen una pantalla.
 *
 * Uso, desde front/:
 *     npx esbuild scripts/verify_spot_check_ui.ts --bundle --platform=node \
 *         --format=cjs --outfile=/tmp/sc.cjs --log-level=error \
 *         --loader:.ts=tsx --loader:.css=empty --define:import.meta='{"env":{}}' \
 *         && node /tmp/sc.cjs
 */

import { renderToStaticMarkup } from 'react-dom/server';
import {
  PanelEvidencia, TarjetaOrigen, ResumenMuestra, TarjetaMuestra, TablaLoLeido,
} from '../src/pages/SpotCheck';
import type {
  SpotCheckQueue, SpotCheckItem, ReporteExactitud, ExactitudPorOrigen, Ticket, Veredicto,
} from '../src/types/api';

const API = process.env.API_URL || 'http://localhost:8000/api/v1';

let fallos = 0;
function check(desc: string, cond: boolean, detalle = '') {
  console.log(`  ${cond ? 'OK   ' : 'FALLA'} ${desc}${!cond && detalle ? `\n         ${detalle}` : ''}`);
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

const noop = () => undefined;
const noopAsync = async () => undefined;

// ---------------------------------------------------------------------------
// Casos de prueba
// ---------------------------------------------------------------------------

/** Un ticket leido correctamente, con todo presente. */
const TICKET_COMPLETO: Ticket = {
  id: '11111111-1111-1111-1111-111111111111',
  company_id: '22222222-2222-2222-2222-222222222222',
  provider_name: 'Tiendas Ramirez SA de CV',
  provider_tax_id: 'TRAM910101XXX',
  total_amount: '1100.00',
  tax_amount: '136.00',
  subtotal: '964.00',
  expense_date: '2025-03-15',
  category: 'alimentos',
  raw_text: 'TIENDAS RAMIREZ\nRFC TRAM910101XXX\nSUBTOTAL 964.00\nIVA 136.00\nTOTAL 1100.00',
  created_at: '2025-03-15T14:30:00Z',
  confidence: '0.970',
  confidence_source: 'llm_validated',
  extraction_status: 'AUTO_APROBADO',
  source_type: 'image',
  source_file: 'ticket-001.jpg',
  validation_errors: null,
  reviewed_by: null,
  reviewed_at: null,
  review_notes: null,
};

const PENDIENTE: SpotCheckItem = {
  ticket: TICKET_COMPLETO,
  spot_check_status: 'PENDIENTE',
  spot_checked_at: null,
  spot_check_notes: null,
  spot_check_wrong_fields: [],
};

function origen(extra: Partial<ExactitudPorOrigen> = {}): ExactitudPorOrigen {
  return {
    origen: 'llm',
    revisados: 0, aciertos: 0, incorrectos: 0, pendientes: 0,
    exactitud: null, intervalo_inferior: null, intervalo_superior: null,
    veredicto: 'SIN_EVIDENCIA',
    motivo_faltante: 'Nadie reviso ningun ticket de esta via.',
    total_revisiones_necesarias: null,
    campo_mas_fallido: null,
    conteo_por_campo: {},
    ...extra,
  };
}

function reporte(extra: Partial<ReporteExactitud> = {}): ReporteExactitud {
  return {
    company_id: null,
    objetivo: 0.96,
    nivel_confianza: 0.95,
    veredicto_global: 'SIN_EVIDENCIA',
    explicacion: 'Aun no hay veredictos: no se puede afirmar ni descartar.',
    por_origen: [origen(), origen({ origen: 'pdf_text' }), origen({ origen: 'rules' })],
    ...extra,
  };
}

// ---------------------------------------------------------------------------

async function main() {
  // =======================================================================
  console.log('\n0) La API real manda la forma que el TypeScript declara');
  // =======================================================================
  // Esta es la parte que un test de render con datos inventados NO puede
  // comprobar. Si el backend agrega o renombra un campo, el TS deja de
  // coincidir y todo lo de abajo pasa con datos que nunca llegan.
  const [resCola, resReporte] = await Promise.all([
    fetch(`${API}/tickets/spot-check?limit=5`),
    fetch(`${API}/tickets/accuracy`),
  ]);
  check(`la cola responde 200 (${resCola.status})`, resCola.status === 200);
  check(`el reporte responde 200 (${resReporte.status})`, resReporte.status === 200);
  if (!resCola.ok || !resReporte.ok) return;

  const colaReal: SpotCheckQueue = await resCola.json();
  const reporteReal: ReporteExactitud = await resReporte.json();

  const claves = (o: object) => Object.keys(o).sort();
  const esperadasCola = [
    'aciertos', 'antiguedad_promedio_dias', 'company_id', 'incorrectos',
    'tickets', 'total_pendientes', 'total_revisados',
  ];
  check(
    'la cola trae los mismos campos que declara SpotCheckQueue',
    JSON.stringify(claves(colaReal)) === JSON.stringify(esperadasCola),
    `API: ${claves(colaReal).join(', ')}\n         TS: ${esperadasCola.join(', ')}`,
  );

  const esperadasReporte = [
    'company_id', 'explicacion', 'nivel_confianza', 'objetivo',
    'por_origen', 'veredicto_global',
  ];
  check(
    'el reporte trae los mismos campos que declara ReporteExactitud',
    JSON.stringify(claves(reporteReal)) === JSON.stringify(esperadasReporte),
    `API: ${claves(reporteReal).join(', ')}\n         TS: ${esperadasReporte.join(', ')}`,
  );

  const esperadasOrigen = [
    'aciertos', 'campo_mas_fallido', 'conteo_por_campo', 'exactitud',
    'incorrectos', 'intervalo_inferior', 'intervalo_superior', 'motivo_faltante',
    'origen', 'pendientes', 'revisados', 'total_revisiones_necesarias', 'veredicto',
  ];
  check(
    'cada origen trae los mismos campos que declara ExactitudPorOrigen',
    reporteReal.por_origen.length > 0
      && claves(reporteReal.por_origen[0]).join() === esperadasOrigen.join(),
    `API: ${claves(reporteReal.por_origen[0] ?? {}).join(', ')}`,
  );
  check(
    'la API manda los tres origenes aunque no tengan datos',
    reporteReal.por_origen.length >= 3,
    `llego con ${reporteReal.por_origen.length}`,
  );

  // =======================================================================
  console.log('\n1) Sin evidencia NO se pinta como 0%');
  // =======================================================================
  // El fallo mas caro de esta pantalla seria mostrar "0% de exactitud" cuando
  // en realidad nadie ha mirado nada. Se lee como un resultadoTERSO y hace
  // pensar que el sistema fallo entero, que es justo lo contrario de cierto.
  const htmlVacio = texto(renderToStaticMarkup(<PanelEvidencia reporte={reporte()} />));
  check('el veredicto global dice Sin evidencia', htmlVacio.includes('Sin evidencia'));
  check('no inventa un porcentaje', !htmlVacio.includes('% de exactitud'));
  check('no dice 0%', !/\b0(\.0)?%/.test(htmlVacio));
  check('la accion si dice que revisar', htmlVacio.toLowerCase().includes('revisar'));

  const htmlOrigenVacio = texto(renderToStaticMarkup(
    <TarjetaOrigen origen={origen({ origen: 'pdf_text', pendientes: 12 })} />,
  ));
  check('el origen sin datos dice "Sin medir"', htmlOrigenVacio.includes('Sin medir'));
  check('el origen sin datos NO dice 0%', !/\b0(\.0)?%/.test(htmlOrigenVacio));
  check('pero si cuenta los pendientes', htmlOrigenVacio.includes('12 sin revisar'));

  // =======================================================================
  console.log('\n2) El intervalo va pegado al porcentaje');
  // =======================================================================
  const conIntervalo = texto(renderToStaticMarkup(<TarjetaOrigen origen={origen({
    origen: 'llm',
    revisados: 1440, aciertos: 1400, incorrectos: 40,
    exactitud: 1400 / 1440,
    intervalo_inferior: 0.9431, intervalo_superior: 0.9820,
    veredicto: 'CUMPLE',
    motivo_faltante: 'La muestra ya sostiene el objetivo.',
    campo_mas_fallido: 'total_amount',
    conteo_por_campo: { total_amount: 22, expense_date: 11 },
  })} />));
  check('muestra el punto medio', conIntervalo.includes('97.2%'));
  check('muestra el limite inferior', conIntervalo.includes('94.3%'));
  check('muestra el limite superior', conIntervalo.includes('98.2%'));
  check('los tres en la misma linea', conIntervalo.includes('97.2% [94.3% - 98.2%]'));
  check('traduce el campo que mas falla', conIntervalo.includes('Total'));
  check('NO enseña el nombre crudo del campo', !conIntervalo.includes('total_amount'));
  check('dice que ya sostiene el objetivo', conIntervalo.includes('Cumple el objetivo'));

  // =======================================================================
  console.log('\n3) Un veredicto desconocido se muestra, no se esconde');
  // =======================================================================
  // Si el backend agrega un veredicto y esta pantalla no lo conoce, lo que
  // tiene que verse es "no lo entiendo". Si se cae en el `default` y pinta
  // verde, alguien lee un OK que no existe.
  const desconocido = texto(renderToStaticMarkup(<TarjetaOrigen origen={origen({
    origen: 'ocr_propietario',
    revisados: 10, aciertos: 10,
    exactitud: 1.0, intervalo_inferior: 0.7, intervalo_superior: 1.0,
    veredicto: 'MUY_BIEN' as Veredicto,
    motivo_faltante: 'Veredicto nuevo del backend.',
  })} />));
  check('nombra el veredicto que no conoce', desconocido.includes('No reconocido: MUY_BIEN'));
  // Visible, no solo en el title del badge: en escritorio nadie pasa el mouse
  // por un badge que no reconoce, y ese es justo el caso que lo necesita.
  check('avisa VISIBLE que la pantalla no lo entiende',
    desconocido.toLowerCase().includes('no conoce')
    && !desconocido.includes('title='),
    'el aviso estaba escondido en un title, que exige pasar el mouse encima');
  check('no lo presenta como un resultado valido', !desconocido.includes('Cumple el objetivo'));

  const panelDesconocido = texto(renderToStaticMarkup(<PanelEvidencia reporte={reporte({
    veredicto_global: 'CASI' as Veredicto,
    explicacion: 'Veredicto nuevo del backend.',
  })} />));
  check('tambien en el veredicto global', panelDesconocido.includes('No reconocido: CASI'));
  check('y ofrece una accion, no un callejon', panelDesconocido.includes('Veredicto no reconocido'));

  // =======================================================================
  console.log('\n4) "No cumple" no pide mas muestra');
  // =======================================================================
  // El limite superior debajo del objetivo significa que el problema es el
  // extractor, no la cantidad de evidencia. Decir "sigue midiendo" ahi manda a
  // la gente a medir algo que no va a cambiar.
  const noCumple = texto(renderToStaticMarkup(<PanelEvidencia reporte={reporte({
    veredicto_global: 'NO_CUMPLE',
    explicacion: 'El limite superior esta en 90.1%, debajo del 96%.',
    por_origen: [origen({ origen: 'vision', revisados: 200, aciertos: 170, incorrectos: 30,
      exactitud: 0.85, intervalo_inferior: 0.79, intervalo_superior: 0.90,
      veredicto: 'NO_CUMPLE', motivo_faltante: 'Acierto por debajo del objetivo.' })],
  })} />));
  check('dice que no cumple', noCumple.includes('No cumple el objetivo'));
  check('dice que hay que arreglar, no medir mas',
    noCumple.includes('arreglar la lectura') && noCumple.includes('Medir mas no lo cambia'));
  check('no sugiere seguir midiendo', !noCumple.includes('Seguir revisando la muestra'));

  // =======================================================================
  console.log('\n5) El conteo recortado se declara');
  // =======================================================================
  // Con 200 pendientes y limit=50, decir "50" hace creer al que mira que
  // quedan 50. La lista y el conteo son dos cosas y la pantalla tiene que
  // decirlo.
  const recortado = texto(renderToStaticMarkup(
    <ResumenMuestra pendientes={200} revisados={340} aciertos={300} incorrectos={40}
      antiguedad={6.4} listados={50} />,
  ));
  check('el conteo global es 200, no 50', recortado.includes('200'));
  check('dice cuantas se estan mostrando', recortado.includes('Se muestran 50 de 200'));
  check('dice cuanto llevan esperando', recortado.includes('6.4'));
  check('los revisados son los 340 reales', recortado.includes('340'));

  const completo = texto(renderToStaticMarkup(
    <ResumenMuestra pendientes={0} revisados={12} aciertos={12} incorrectos={0}
      antiguedad={null} listados={0} />,
  ));
  check('sin pendientes no avisa de recorte', !completo.includes('Se muestran'));
  check('sin antiguedad no inventa dias', !completo.includes('Esperan hace'));
  check('cero no coincidence no se pinta como problema',
    completo.includes('0') && !completo.includes('FALLA'));

  // =======================================================================
  console.log('\n6) La tarjeta muestra lo leido, incluido lo que no se leyo');
  // =======================================================================
  // Un veredicto dado sin ver la lectura no mide nada. Y un campo ausente que
  // no se ve, no se puede marcar como mal leido.
  const conTexto = texto(renderToStaticMarkup(
    <TarjetaMuestra item={PENDIENTE} onRefresh={noopAsync} />,
  ));
  check('muestra el total leido', conTexto.includes('1100.00'));
  check('muestra el IVA leido', conTexto.includes('136.00'));
  check('muestra el SUBTOTAL leido', conTexto.includes('964.00'),
    'sin el, la pregunta "¿se leyo bien el subtotal?" no tiene con que responderse');
  check('deja ver el texto crudo', conTexto.includes('lo que leyó el sistema'));
  check('ofrece los dos veredictos',
    conTexto.includes('Sí coincidió') && conTexto.includes('No coincidió'));
  check('aclara, en la tarjeta, que aqui no se corrige el ticket',
    conTexto.includes('no cambia el ticket') && conTexto.includes('cola de revisión'),
    'quien llega con la costumbre de la cola de revision busca el boton de arreglar;'
    + ' si la tarjeta no dice que no lo hay, la ausencia se lee como olvido');

  // Ojo con la forma del check: se mira la CELDA del subtotal, no el texto
  // entero. `!texto.includes('0.00')` sobre la tarjeta completa da falso por
  // construccion, porque el total "1100.00" CONTIENE la cadena "0.00". Un
  // check asi no vigila el bug que dice vigilar: falla siempre, o nunca, por
  // una coincidencia de caracteres y no por la razon.
  const sinSubtotal = texto(renderToStaticMarkup(
    <TablaLoLeido ticket={{ ...TICKET_COMPLETO, subtotal: null }} />,
  ));
  check('un subtotal ausente se DICE ausente', sinSubtotal.includes('Subtotal no se leyó'));
  check('la celda del subtotal no dice 0.00', !sinSubtotal.includes('Subtotal 0.00'),
    'un 0.00 haria que "subtotal + IVA == total" pareciera cuadrar');
  // Y el control: el mismo render con subtotal presente SI lo dice.
  const conSubtotal = texto(renderToStaticMarkup(
    <TablaLoLeido ticket={TICKET_COMPLETO} />,
  ));
  check('el control: con subtotal presente, la celda lo muestra',
    conSubtotal.includes('Subtotal 964.00'));

  const sinTexto = texto(renderToStaticMarkup(<TarjetaMuestra
    item={{ ...PENDIENTE, ticket: { ...TICKET_COMPLETO, raw_text: null } }}
    onRefresh={noopAsync} />));
  check('sin texto crudo avisa que no hay contra que comparar',
    sinTexto.includes('no guardó texto'));

  // El proveedor desconocido no se presenta como un nombre.
  const sinProv = texto(renderToStaticMarkup(<TarjetaMuestra
    item={{ ...PENDIENTE, ticket: { ...TICKET_COMPLETO, provider_name: 'Unknown Provider' } }}
    onRefresh={noopAsync} />));
  check('el proveedor desconocido se avisa', sinProv.includes('Sin proveedor identificado'));
  check('no se muestra el literal crudo', !sinProv.includes('Unknown Provider'));

  // Un ticket ya revisado muestra el resultado, y los campos que fallaron con
  // su etiqueta en espanol.
  const revisado = texto(renderToStaticMarkup(<TarjetaMuestra item={{
    ...PENDIENTE,
    spot_check_status: 'INCORRECTO',
    spot_checked_at: '2025-04-01T10:00:00Z',
    spot_check_wrong_fields: ['total_amount', 'campo_inventado'] as never,
    spot_check_notes: 'La foto estaba cortada por la mitad.',
  }} onRefresh={noopAsync} />));
  check('dice que no coincidio', revisado.includes('No coincidía'));
  check('traduce el campo que fallo a "Total"', revisado.includes('Total'));
  check('un campo desconocido se muestra crudo, para poder reportarlo',
    revisado.includes('campo no reconocido: campo_inventado'));
  check('muestra la nota', revisado.includes('La foto estaba cortada'));
  check('ya revisado no ofrece volver a decidir',
    !revisado.includes('Sí coincidió'));

  // =======================================================================
  console.log('\n7) El estado de la cola que devuelve la API se renderiza');
  // =======================================================================
  if (colaReal.tickets.length > 0) {
    for (const item of colaReal.tickets) {
      let ok = true;
      let detalle = '';
      try {
        const h = renderToStaticMarkup(<TarjetaMuestra item={item} onRefresh={noopAsync} />);
        ok = h.length > 100;
      } catch (e) {
        ok = false;
        detalle = (e as Error).message;
      }
      check(`  ${item.ticket.source_file ?? item.ticket.id}`, ok, detalle);
    }
  } else {
    // Sin sembrar, la base esta vacia. Se dice, en vez de fingir que se
    // reviso algo que no hay.
    check('la base no tiene muestra sembrada (se revisa con los casos de arriba)', true);
    console.log('         (cola vacia: los casos 1-6 usan payloads a proposito)');
  }

  const htmlColaReal = texto(renderToStaticMarkup(<PanelEvidencia reporte={reporteReal} />));
  check('el reporte real se renderiza sin reventar', htmlColaReal.length > 40);
  check('el reporte real no inventa porcentajes sin datos',
    reporteReal.por_origen.every((o) => o.exactitud !== null || !htmlColaReal.includes('0.0%')));

  console.log(`\n${'='.repeat(62)}`);
  if (fallos) {
    console.log(`FALLARON ${fallos} comprobaciones de la pantalla.`);
    return 1;
  }
  console.log('La pantalla del muestreo renderiza lo que dice y no afirma lo que no sabe.');
  return 0;
}

main().then(c => process.exit(c)).catch(e => {
  console.error('La verificacion revento:', e);
  process.exit(1);
});
