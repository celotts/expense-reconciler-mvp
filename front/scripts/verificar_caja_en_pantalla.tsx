/**
 * Que la pantalla de caja chica pinte lo que el backend afirmo.
 *
 * `tsc` pasa con un `undefined` en pantalla, asi que compilar no basta. Este
 * script monta la pagina COMPLETA con datos que tienen la forma del JSON real
 * de `/caja-chica` y `/duplicados`, y mira el HTML resultante.
 *
 * Lo que protege:
 *
 * 1. `saldo_bajo_umbral` se lee del backend. Con `saldo 150 < umbral 200` tiene
 *    que salir el aviso; si el front lo calculara con `<` en vez de `<=`, o lo
 *    inventara a partir de los movimientos, aqui se ve.
 * 2. Un ticket vinculado muestra el banco que declaro el movimiento.
 * 3. El total de duplicados sale del resumen del servidor. El front no suma
 *    los grupos: sumarlos contaria dos veces el mismo comprobante.
 * 4. La fecha de negocio no se corre un dia por zona horaria.
 * 5. Una caja cerrada no ofrece registrar movimientos.
 *
 * No entra en `npm run verify` mientras `verify_login` siga fallando contra la
 * API real (known-issues §33). Se corre con `npm run verify:caja`.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CajaChica } from '../src/pages/CajaChica';
import { SesionProvider } from '../src/contextos/Sesion';
import type {
  Company, CajaChicaConMovimientos, ResumenDuplicados,
} from '../src/types/api';

const fallos: string[] = [];

function comprobar(nombre: string, condicion: boolean, detalle = '') {
  if (condicion) {
    console.log(`    ok  ${nombre}`);
  } else {
    console.log(`    ??  ${nombre}${detalle ? `  (${detalle})` : ''}`);
    fallos.push(nombre);
  }
}

const EMPRESA: Company = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Comercio de Prueba SA de CV',
  tax_id: 'CPR010101ABC',
  created_at: '2026-09-01T10:00:00Z',
};

const CAJA_ABIERTA: CajaChicaConMovimientos = {
  id: '22222222-2222-4222-8222-222222222222',
  company_id: EMPRESA.id,
  responsable_id: '33333333-3333-4333-8333-333333333333',
  nombre: 'Caja de oficina',
  monto_inicial: '1000.00',
  saldo_actual: '150.00',
  umbral_alerta: '200.00',
  estado: 'abierta',
  opened_at: '2026-10-01T09:00:00Z',
  closed_at: null,
  // Lo dice el servidor. El front no lo decide mirando los movimientos.
  saldo_bajo_umbral: true,
  movimientos: [
    {
      id: '44444444-4444-4444-8444-444444444444',
      company_id: EMPRESA.id,
      caja_chica_id: '22222222-2222-4222-8222-222222222222',
      ticket_id: null,
      tipo: 'reposicion',
      origen_fondo: 'banco',
      banco_nombre: 'Banco Prueba',
      referencia_bancaria: 'TRX-991',
      monto: '500.00',
      notas: 'Reposicion de la semana',
      created_by: '33333333-3333-4333-8333-333333333333',
      created_at: '2026-10-02T12:30:00Z',
    },
    {
      id: '55555555-5555-4555-8555-555555555555',
      company_id: EMPRESA.id,
      caja_chica_id: '22222222-2222-4222-8222-222222222222',
      ticket_id: '66666666-6666-4666-8666-666666666666',
      tipo: 'gasto_ticket',
      origen_fondo: null,
      banco_nombre: null,
      referencia_bancaria: null,
      monto: '279.93',
      notas: null,
      created_by: '33333333-3333-4333-8333-333333333333',
      created_at: '2026-10-03T13:00:00Z',
    },
  ],
};

const CAJA_CERRADA: CajaChicaConMovimientos = {
  ...CAJA_ABIERTA,
  id: '77777777-7777-4777-8777-777777777777',
  nombre: 'Caja de octubre',
  saldo_actual: '900.00',
  umbral_alerta: '200.00',
  saldo_bajo_umbral: false,
  estado: 'cerrada',
  closed_at: '2026-10-31T20:15:00Z',
  movimientos: [],
};

const RESUMEN: ResumenDuplicados = {
  company_id: EMPRESA.id,
  // Dos grupos de dos comprobantes: sumar las filas daria el doble.
  grupos: [
    {
      hash_duplicado: 'abc123def456',
      monto_total: '279.93',
      tickets: [
        {
          ticket_id: '66666666-6666-4666-8666-666666666666',
          company_id: EMPRESA.id,
          provider_name: 'GRUPO COMERCIAL DSW S.A. DE C.V.',
          provider_tax_id: 'GCD170101GS6',
          folio: '114348',
          expense_date: '2026-10-01',
          total_amount: '279.93',
          hash_duplicado: 'abc123def456',
          estado_conciliacion: 'PERFECT',
          source_file: 'IMG_4409.HEIC',
          created_at: '2026-10-01T12:00:00Z',
        },
        {
          ticket_id: '88888888-8888-4888-8888-888888888888',
          company_id: EMPRESA.id,
          provider_name: 'GRUPO COMERCIAL DSW S.A. DE C.V.',
          provider_tax_id: 'GCD170101GS6',
          folio: '114348',
          expense_date: '2026-10-01',
          total_amount: '279.93',
          hash_duplicado: 'abc123def456',
          estado_conciliacion: null,
          source_file: 'IMG_4409_copia.HEIC',
          created_at: '2026-10-02T12:00:00Z',
        },
      ],
    },
  ],
  total_conciliado_sin_duplicados: '4321.00',
  advertencia: 'Hay comprobantes repetidos: el total conciliado ya los cuenta una sola vez.',
};

function pintar(caja: CajaChicaConMovimientos, pestana: 'cajas' | 'duplicados' = 'cajas'): string {
  // `ComponentProps` lee los props REALES del componente: un `as any` aqui
  // dejaba pasar un prop renombrado y el verificador entonces miraba una
  // pantalla montada con valores por omision, no con los datos de abajo.
  const props: React.ComponentProps<typeof CajaChica> = {
    companiesIniciales: [EMPRESA],
    cajasIniciales: [caja],
    cajaInicial: caja,
    resumenInicial: RESUMEN,
    ...(pestana === 'duplicados' ? { pestanaInicial: pestana } : {}),
  };
  return renderToStaticMarkup(
    React.createElement(
      SesionProvider,
      null,
      React.createElement(CajaChica, props),
    ),
  );
}

console.log('La pantalla de caja chica se ve en pantalla:');

const htmlCajas = pintar(CAJA_ABIERTA);
comprobar('la pantalla monta con datos iniciales', htmlCajas.includes('Caja de oficina'));
comprobar('el saldo sale del backend', htmlCajas.includes('$150.00'));
comprobar('el aviso de umbral aparece con role=alert', htmlCajas.includes('role="alert"') && htmlCajas.includes('Saldo bajo el umbral'));
comprobar('el nombre del banco del movimiento es visible', htmlCajas.includes('Banco: Banco Prueba'));
comprobar('la fecha de negocio no se corre un dia', htmlCajas.includes('01/10/2026'), 'no aparecio 01/10/2026');
comprobar('el total del ticket se muestra con Dos decimales', htmlCajas.includes('$279.93'));
comprobar('la caja abierta permite registrar movimientos', htmlCajas.includes('Registrar movimiento'));

const htmlCerrada = pintar(CAJA_CERRADA);
comprobar('el estado cerrado se nombra', htmlCerrada.includes('Cerrada'));
comprobar('una caja cerrada deshabilita registrar movimientos', htmlCerrada.includes('disabled=""'));
comprobar('sin aviso de umbral no se inventa uno', !htmlCerrada.includes('Saldo bajo el umbral'));

// La pestana de duplicados se monta con `pestanaInicial`: el verificador no
// puede hacer clic en `renderToStaticMarkup`. Sin el prop, esta mitad de la
// pantalla no se pintaba y las seis comprobaciones de abajo fallaban aunque la
// pagina estuviera bien — un test que no ejecuta la rama no prueba la rama.
const htmlDuplicados = pintar(CAJA_ABIERTA, 'duplicados');
comprobar('la busqueda de huella fiscal esta en pantalla', htmlDuplicados.includes('Comprobar una huella fiscal'));
comprobar('el boton de buscar duplicado existe', htmlDuplicados.includes('Buscar duplicado'));
comprobar('la advertencia del resumen aparece', htmlDuplicados.includes('Hay comprobantes repetidos'));
comprobar('el total sin duplicados viene del backend', htmlDuplicados.includes('$4,321.00'));
comprobar('el grupo nombra los dos comprobantes', htmlDuplicados.includes('2 comprobantes'));
comprobar('la huella fiscal se muestra', htmlDuplicados.includes('abc123def456'));

if (fallos.length) {
  console.error(`\n${fallos.length} comprobacion(es) fallida(s): ${fallos.join(', ')}`);
  process.exitCode = 1;
} else {
  console.log('\nTodas las comprobaciones de caja chica pasaron.');
}
