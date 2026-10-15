// Mide el desbordamiento horizontal real de cada pantalla, en tres anchos.
//
// POR QUE MEDIR Y NO MIRAR
// ========================
// Un `--screenshot` de 8 pantallas x 3 anchos son 24 imagenes, y responder
// "¿esta bien?" sobre cada una es una opinion. El defecto que importa en
// responsive es casi siempre el mismo y es medible: un elemento mas ancho que
// su contenedor hace que TODA la pagina se pueda desplazar de lado, y eso se
// lee como un numero.
//
// Por eso el script devuelve, para cada pantalla y cada ancho:
//   - `overflowPx`   : cuanto se sale la pagina (0 = bien)
//   - `culpables`    : los 5 elementos que mas sobresalen, con su selector
//
// Y `culpables` es lo que convierte el numero en una accion: saber que la tabla
// de Tickets se sale 180px no dice nada; saber que lo hace el `<div>` dentro de
// `overflow-x-auto` de la tabla SI, porque ese padre es el que deberia
// contenerlo y no lo hace.
//
// LOS ANCHOS
// ----------
//   390  iPhone 14 / 15. El angosto que de verdad se usa.
//   768  tablet vertical. El breakpoint `md`, donde `flex-col` y no.
//   1024 laptop chico. JUSTO en el breakpoint `lg` del sidebar (1024px), que es
//        donde un `w-64` fijo aparece o desaparece: un error de un pixel de
//        diferencia aqui se ve en pantalla y no se nota leyendo el codigo.

const PAGINAS = [
  { key: 'dashboard',      label: 'Inicio' },
  { key: 'companies',      label: 'Empresas' },
  { key: 'tickets',        label: 'Tickets' },
  { key: 'scan',           label: 'Escáner' },
  { key: 'review',         label: 'Cola de revisión' },
  { key: 'spotcheck',      label: 'Muestreo' },
  { key: 'bank',           label: 'Banco' },
  { key: 'reconciliations', label: 'Conciliación' },
];

const ANCHOS = [
  { w: 390,  h: 844,  nombre: 'movil-390' },
  { w: 768,  h: 1024, nombre: 'tablet-768' },
  { w: 1024, h: 768,  nombre: 'laptop-1024' },
];

const EMAIL = 'prueba@despacho.mx';
const PASSWORD = 'Prueba-2026-mx';

// Selector legible y corto. Se sube por los ancestros hasta 4 niveles y para en
// el primero que tenga clase o id, porque un `div:nth-child(3) > div:nth-child(2)`
// no sirve: cambia en cuanto se inserta un nodo y entonces el reporte deja de
// ser comparable entre corridas.
const DESCRIPTOR = `
  (() => {
    const clases = (el) => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      if (el.className && typeof el.className === 'string') {
        const c = el.className.trim().split(/\\s+/).slice(0, 3).join('.');
        if (c) s += '.' + c;
      }
      return s;
    };
    return (el) => {
      const partes = [];
      let n = el;
      for (let i = 0; i < 4 && n && n !== document.body; i++) {
        partes.unshift(clases(n));
        n = n.parentElement;
      }
      return partes.join(' > ');
    };
  })()
`;

async function medir(page, ancho, alto) {
  await page.setViewportSize({ width: ancho, height: alto });
  // Un frame para que el layout se asiente tras el cambio de tamano.
  await page.waitForTimeout(350);

  return page.evaluate(({ descriptorSrc, anchoVentana }) => {
    const describe = eval(descriptorSrc);
    const de = document.documentElement;
    const overflowPx = Math.max(0, de.scrollWidth - anchoVentana);

    // Los culpables: elementos cuyo borde derecho se sale del ancho de la
    // ventana. Se filtra el arbol para no reportar 400 hijos de un mismo
    // contenedor — si un padre ya se sale, reportar a los hijos es ruido.
    const todos = Array.from(document.querySelectorAll('body *'));
    const culpables = todos
      .filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > anchoVentana + 1;
      })
      .sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)
      .slice(0, 5)
      .map(el => {
        const r = el.getBoundingClientRect();
        return {
          sel: describe(el),
          right: Math.round(r.right),
          width: Math.round(r.width),
          // Se annota si un ancestro ya tiene scroll horizontal: si lo tiene,
          // el elemento NO esta romper la pagina, es contenido de una tabla
          // que ya esta dentro de un `overflow-x-auto`. Eso NO es un defecto.
          enContenedorConScroll: (() => {
            let n = el.parentElement;
            while (n && n !== document.body) {
              const ov = getComputedStyle(n).overflowX;
              if (ov === 'auto' || ov === 'scroll' || ov === 'hidden') return true;
              n = n.parentElement;
            }
            return false;
          })(),
        };
      });

    return { overflowPx, culpables, scrollWidth: de.scrollWidth };
  }, { descriptorSrc: DESCRIPTOR, anchoVentana: ancho });
}

export default async function run(page) {
  const resultado = { pantallas: {}, errores: [] };

  // --- Login ---------------------------------------------------------------
  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  const campoCorreo = page.locator('input[type=email], input[name=email]').first();
  const campoPass = page.locator('input[type=password]').first();

  if (await campoCorreo.count() === 0 && await campoPass.count() === 0) {
    resultado.errores.push('no aparecio el formulario de login: la pagina no monto');
    return resultado;
  }

  await campoCorreo.fill(EMAIL);
  await campoPass.fill(PASSWORD);
  await page.locator('button[type=submit], form button').first().click();
  await page.waitForTimeout(2500);

  // Si despues del login sigue el formulario, el acceso fallo y todas las
  // mediciones de abajo serian del login y no de las pantallas.
  if (await campoPass.count() > 0) {
    const texto = await page.locator('body').innerText();
    resultado.errores.push('el login no entro; sigue en la pantalla de acceso. ' +
      'Texto visible: ' + texto.slice(0, 200).replace(/\s+/g, ' '));
    return resultado;
  }

  // --- Recorrido ------------------------------------------------------------
  for (const ancho of ANCHOS) {
    const porPagina = {};

    // EL VIEWPORT SE FIJA ANTES de navegar, no despues. El primer intento lo
    // dejaba para `medir()`, y el boton de la hamburguesa no existia en el DOM
    // visible: el header lleva `lg:hidden`, y a 1280px de ancho —el viewport por
    // omision de Playwright— esta oculto. El clic se quedaba 30 s esperando a
    // un elemento que existe en el DOM pero tiene `display:none`.
    //
    // El orden importa porque `lg:hidden` es un breakpoint de 1024: en 390 hay
    // que abrir el drawer para cambiar de pantalla, y en 1024 ya no hay drawer.
    // Decidir eso con el viewport equivocado produce un falso "no se encontro
    // el boton de navegacion" en las tres pantallas.
    await page.setViewportSize({ width: ancho.w, height: ancho.h });
    await page.waitForTimeout(300);

    for (const pag of PAGINAS) {
      // En movil el menu lateral es un drawer: hay que abrirlo para llegar a la
      // pantalla. En >=1024 el sidebar esta fijo y el boton no existe.
      if (ancho.w < 1024) {
        const hamburguesa = page.locator('header button:visible').first();
        if (await hamburguesa.count() > 0) {
          await hamburguesa.click();
          await page.waitForTimeout(450);
        }
      }
      const boton = page.getByRole('button', { name: pag.label, exact: true }).first();
      if (await boton.count() === 0) {
        resultado.errores.push(`${ancho.nombre}/${pag.label}: no se encontro el boton de navegacion`);
        continue;
      }
      await boton.click();
      await page.waitForTimeout(1500);

      porPagina[pag.key] = await medir(page, ancho.w, ancho.h);
    }
    resultado.pantallas[ancho.nombre] = porPagina;
  }

  return resultado;
}
