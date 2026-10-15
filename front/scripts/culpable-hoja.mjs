// Encuentra el elemento MAS PROFUNDO que se sale del ancho de la ventana.
//
// POR QUE NO SIRVE EL REPORTE DEL PRIMER SCRIPT
// =============================================
// `midir-responsive.mjs` devuelve los elementos mas salidos, y a 390 en el
// tablero eso es `div.grid > div.lg:col-span-3`: 403px. Correcto, y no sirve
// para nada, porque ese div no es mas ancho que su padre — lo que lo empuja son
// sus hijos, y cada uno de ellos tiene a su vez un culpable. Es una cadena de
// cuatro niveles y el primer nivel no dice nada.
//
// Este script CAMBIA el criterio: un elemento es culpable si se sale **y no
// tiene ningun hijo que tambien se salga**. Asi el reporte senala la hoja del
// arbol, que es donde esta el `min-w-` o el texto que no parte.
//
// QUE BUSCA EN LA HOJA, Y POR QUE CADA COSA:
//   - `min-width` explicito  -> `min-w-*` de Tailwind, el motivo mas comun
//   - scrollWidth > clientWidth en la hoja: la hoja tiene contenido mas ancho
//     que ella, o sea que el problema es de CONTENIDO y no de la caja
//   - texto largo sin partir: se comprueba con `white-space` y con que el texto
//     del nodo sea mas largo que la caja
//
// Mide ademas el ancho REAL de cada hoja, que es el dato util: "esta caja pide
// 403px" ya dice que hay algo de 403px adentro.

export default async function run(page) {
  const PAGINAS = [
    { key: 'dashboard', label: 'Inicio' },
    { key: 'review',     label: 'Cola de revisión' },
  ];
  const ANCHOS = [{ w: 390, h: 844, nombre: 'movil-390' }];
  const out = {};

  // El login va aqui y no en un script aparte porque cada invocacion abre un
  // navegador nuevo: una sesion nombrada serviria, pero entonces este script
  // dejaria de ser corrible por separado y el fallo seria "no encuentro el boton
  // de Inicio" en vez de "no entro".
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const correo = page.locator('input[type=email]').first();
  const pass = page.locator('input[type=password]').first();
  if (await pass.count() > 0) {
    await correo.fill('prueba@despacho.mx');
    await pass.fill('Prueba-2026-mx');
    await page.locator('button[type=submit], form button').first().click();
    await page.waitForTimeout(2500);
  }

  const sonda = () => {
    const vw = window.innerWidth;
    const seSale = el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.right > vw + 1;
    };
    const todos = Array.from(document.querySelectorAll('body *'));
    const fuera = todos.filter(seSale);
    const conjunto = new Set(fuera);

    // Una hoja es un culpable que se sale y cuyos hijos NO se salen.
    const hojas = fuera.filter(el => !Array.from(el.children).some(c => conjunto.has(c)));

    const describir = el => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      if (el.className && typeof el.className === 'string') {
        s += '.' + el.className.trim().split(/\s+/).slice(0, 4).join('.');
      }
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        sel: s.slice(0, 140),
        w: Math.round(r.width),
        right: Math.round(r.right),
        minWidth: cs.minWidth,
        whiteSpace: cs.whiteSpace,
        overflowX: cs.overflowX,
        // El texto propio, no el de los hijos: si esto es largo y la caja no
        // parte, el problema es una palabra que no cabe.
        texto: (Array.from(el.childNodes)
          .filter(n => n.nodeType === 3)
          .map(n => n.textContent.trim())
          .join(' ')).slice(0, 60),
        // Si la caja pide mas de lo que le dieron, algo dentro la estira.
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      };
    };

    return {
      vw,
      scrollWidth: document.documentElement.scrollWidth,
      hojas: hojas.slice(0, 12).map(describir),
    };
  };

  for (const ancho of ANCHOS) {
    await page.setViewportSize({ width: ancho.w, height: ancho.h });
    await page.waitForTimeout(300);
    out[ancho.nombre] = {};
    for (const pag of PAGINAS) {
      const ham = page.locator('header button:visible').first();
      if (await ham.count() > 0) { await ham.click(); await page.waitForTimeout(400); }
      await page.getByRole('button', { name: pag.label, exact: true }).first().click();
      await page.waitForTimeout(1500);
      out[ancho.nombre][pag.key] = await page.evaluate(sonda);
    }
  }
  return out;
}
