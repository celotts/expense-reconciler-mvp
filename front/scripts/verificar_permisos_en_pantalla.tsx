/** Lo que un `viewer` ve y lo que NO puede tocar, medido en HTML renderizado.
 *
 *  `tsc` pasa con un boton que no debiera estar, y el menu tampoco basta: el
 *  menu decide a que pagina se entra, no que acciones se ofrecen dentro. Aqui
 *  se montan las tarjetas con un contexto de `viewer` REAL —el rol y los
 *  permisos que devuelve `/auth/empresas`— y se lee el HTML.
 *
 *  La contraprueba importa tanto como la prueba: con permisos de administrador
 *  los mismos botones tienen que aparecer. Si desaparecieran por falta de
 *  datos, el verificador daria verde con la pantalla vacia.
 *
 *  Y hay un tercer caso: `rol: 'owner'` con `permisos: ['leer']`. El front no
 *  puede conceder una accion que el servidor no mando, aunque el rol local
 *  diga lo contrario. Es la direccion en que se resuelve una divergencia.
 *
 *  Se corre con `npm run verify:permisos`.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';

import { EmpresaContext, type EmpresaContextValue } from '../src/contextos/EmpresaContexto';
import { SesionProvider } from '../src/contextos/Sesion';
import { TicketEnCola } from '../src/pages/ReviewQueue';
import { TarjetaMuestra } from '../src/pages/SpotCheck';
import { CajaChica } from '../src/pages/CajaChica';
import type { Ticket, SpotCheckItem } from '../src/types/api';
import type { CajaChicaConMovimientos } from '../src/services/api';
import type { Company } from '../src/types/api';

let fallos = 0;
function comprobar(nombre: string, condicion: boolean, detalle = ''): void {
  if (condicion) {
    console.log(`    ok   ${nombre}`);
  } else {
    fallos += 1;
    console.error(`    FAIL ${nombre}${detalle ? `: ${detalle}` : ''}`);
  }
}

const EMPRESA_ID = '11111111-1111-4111-8111-111111111111';
const EMPRESA: Company = {
  id: EMPRESA_ID,
  name: 'Comercio de Prueba SA de CV',
  tax_id: 'CPR010101ABC',
  created_at: '2026-09-01T10:00:00Z',
};

const TICKET: Ticket = {
  id: '22222222-2222-4222-8222-222222222222',
  company_id: EMPRESA_ID,
  provider_name: 'Comercio de Prueba SA de CV',
  provider_tax_id: 'CPR010101ABC',
  total_amount: '234.00',
  tax_amount: '8.14',
  subtotal: '217.27',
  ieps_amount: '8.59',
  expense_date: '2026-10-01',
  category: 'alimentos',
  raw_text: 'SUBTOTAL 217.27 IVA 8.14 IEPS 8.59 TOTAL 234.00',
  items: null,
  created_at: '2026-10-01T12:00:00Z',
  confidence: '0.980',
  confidence_source: 'llm',
  extraction_status: 'REQUIERE_REVISION',
  source_type: 'image',
  source_file: 'ticket.jpg',
  source_hash: 'abc123',
  tiene_documento: false,
  documento_url: null,
  documento_tamano: null,
  validation_errors: null,
  reviewed_by: null,
  reviewed_at: null,
  review_notes: null,
  pais: 'MX',
  identificacion_fiscal: null,
  folio: null,
  metodo_pago: null,
  link_facturacion: null,
  hash_duplicado: null,
  advertencia_duplicado_fiscal: null,
  estado_conciliacion: null,
  periodo_cerrado_override: false,
  periodo_cerrado_motivo: null,
  periodo_cerrado_autorizado_por: null,
  periodo_cerrado_autorizado_at: null,
};

const MUESTRA: SpotCheckItem = {
  ticket: { ...TICKET, extraction_status: 'AUTO_APROBADO' },
  spot_check_status: 'PENDIENTE',
  spot_checked_at: null,
  spot_check_notes: null,
  spot_check_wrong_fields: [],
};

const CAJA: CajaChicaConMovimientos = {
  id: '33333333-3333-4333-8333-333333333333',
  company_id: EMPRESA_ID,
  responsable_id: '44444444-4444-4444-8444-444444444444',
  nombre: 'Caja de oficina',
  monto_inicial: '1000.00',
  saldo_actual: '900.00',
  umbral_alerta: '200.00',
  estado: 'abierta',
  opened_at: '2026-10-01T09:00:00Z',
  closed_at: null,
  saldo_bajo_umbral: false,
  movimientos: [],
};

function contexto(rol: string, permisos: string[]): EmpresaContextValue {
  const membresia = {
    company_id: EMPRESA_ID, name: EMPRESA.name, tax_id: EMPRESA.tax_id, rol, permisos,
  };
  return {
    empresas: [membresia],
    empresaActiva: membresia,
    rol,
    permisos,
    cargando: false,
    necesitaEleccion: false,
    error: null,
    elegirEmpresa: () => undefined,
  };
}

function conContexto(valor: EmpresaContextValue, hijo: React.ReactNode): string {
  return renderToStaticMarkup(
    React.createElement(EmpresaContext.Provider, { value: valor }, hijo),
  );
}

const viewer = contexto('viewer', ['leer']);
const admin = contexto('admin', [
  'leer', 'crear', 'editar', 'borrar', 'aprobar', 'conciliar',
  'exportar', 'cerrar', 'administrar', 'auditar',
]);
// Si el backend dice solo `leer`, el rol local no amplia la autoridad.
const ownerSinPermisos = contexto('owner', ['leer']);

const noop = () => undefined;
const colaViewer = conContexto(viewer,
  <TicketEnCola ticket={TICKET} editando={false} onEditar={noop}
    onCancelarEdicion={noop} onDescartar={noop} onRefresh={noop} />);
const colaAdmin = conContexto(admin,
  <TicketEnCola ticket={TICKET} editando={false} onEditar={noop}
    onCancelarEdicion={noop} onDescartar={noop} onRefresh={noop} />);

console.log('La cola de revision: se lee, pero no se firma sin permiso:');
comprobar('viewer ve los datos del ticket', colaViewer.includes('Comercio de Prueba'));
for (const boton of ['Corregir', 'Aprobar', 'Descartar']) {
  comprobar(`viewer no ve ${boton}`, !colaViewer.includes(`>${boton}<`));
  comprobar(`admin ve ${boton}`, colaAdmin.includes(`>${boton}<`));
}

const muestraViewer = conContexto(viewer, <TarjetaMuestra item={MUESTRA} onRefresh={noop} />);
const muestraAdmin = conContexto(admin, <TarjetaMuestra item={MUESTRA} onRefresh={noop} />);
console.log('El muestreo: ver la evidencia no es registrar el veredicto:');
comprobar('viewer ve la evidencia leida', muestraViewer.includes('Confianza'));
comprobar(
  'viewer no ve los botones de veredicto',
  !muestraViewer.includes('>Sí coincidió<') && !muestraViewer.includes('>No coincidió<'),
);
comprobar('admin ve "Sí coincidió"', muestraAdmin.includes('>Sí coincidió<'));

function cajaHtml(valor: EmpresaContextValue, estado: 'abierta' | 'en_revision'): string {
  const caja = { ...CAJA, estado };
  return conContexto(valor,
    React.createElement(SesionProvider, null,
      React.createElement(CajaChica, {
        companiesIniciales: [EMPRESA],
        cajasIniciales: [caja],
        cajaInicial: caja,
      })));
}

const cajaViewer = cajaHtml(viewer, 'abierta');
const cajaAdmin = cajaHtml(admin, 'abierta');
const cajaRevisionViewer = cajaHtml(viewer, 'en_revision');
const cajaRevisionAdmin = cajaHtml(admin, 'en_revision');
console.log('Caja chica: mirar el saldo no es abrir, mover ni cerrar:');
comprobar('viewer ve el saldo', cajaViewer.includes('Saldo actual'));
comprobar('viewer no ve "Nueva caja"', !cajaViewer.includes('Nueva caja'));
comprobar('admin ve "Nueva caja"', cajaAdmin.includes('Nueva caja'));
comprobar('viewer no ve "Registrar movimiento"', !cajaViewer.includes('Registrar movimiento'));
comprobar('admin ve "Registrar movimiento"', cajaAdmin.includes('Registrar movimiento'));
comprobar('viewer no ve "Enviar a revision"', !cajaViewer.includes('Enviar a revisión'));
comprobar('viewer no ve "Cerrar caja"', !cajaRevisionViewer.includes('Cerrar caja'));
comprobar('admin ve "Cerrar caja"', cajaRevisionAdmin.includes('Cerrar caja'));

const colaOwnerSinPermisos = conContexto(ownerSinPermisos,
  <TicketEnCola ticket={TICKET} editando={false} onEditar={noop}
    onCancelarEdicion={noop} onDescartar={noop} onRefresh={noop} />);
console.log('Los permisos mandados por el servidor ganan al rol local:');
comprobar(
  'owner con permisos [leer] no ve botones de escritura',
  colaOwnerSinPermisos.includes('Comercio de Prueba')
    && !colaOwnerSinPermisos.includes('>Corregir<')
    && !colaOwnerSinPermisos.includes('>Aprobar<'),
);

if (fallos) {
  console.error(`\n${fallos} comprobacion(es) fallida(s).`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de permisos en pantalla pasaron.');
}
