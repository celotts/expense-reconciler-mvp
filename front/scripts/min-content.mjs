// Encuentra el elemento cuyo MIN-CONTENT empuja el grid a 403px en un viewport
// de 390.
//
// POR QUE NO BASTA CON "LO QUE SE SALE"
// ======================================
// El desborde del tablero no lo causa un elemento mas ancho que su caja: lo
// causa un elemento cuyo `min-content` es mas ancho que el hueco disponible.
// Con `display:grid` y `grid-template-columns` sin declarar, la columna implicita
// se dimensiona con `auto`, y el `auto` MINIMO es el min-content del contenido.
// Un solo texto con `white-space:nowrap` de 400px vuelve ancha a la columna
// aunque todas las cajas midan 342.
//
// Por eso medir `getBoundingClientRect()` no lo encuentra: el elemento culpable
// mide 302px de ancho real y aun asi IMPONE 403. Lo que se necesita es su
// min-content, que es una propiedad de su CONTENIDO, no de su geometria.
//
// COMO SE MIDE EL MIN-CONTENT
// ===========================
// No hay API para el. Se clona el subarbol en un contenedor de ancho
// `min-content` y se lee el ancho que el navegador le da. Es lo mismo que hace
// el motor al resolver `min-width:auto`, con la ventaja de que se puede atribuir
// a un nodo concreto.
//
// Se mide sobre los candidatos —los que tienen `white-space:nowrap` o
// `truncate`— porque son los unicos que pueden tener un min-content mayor que su
// ancho visible: cualquier otro elemento parte su texto en puntos de corte que
// el navegador si encuentra.

export default async function run(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);

  const correo = page.locator('input[type=email]').first();
  if (await page.locator('input[type=password]').count() > 0) {
    await correo.fill('prueba@despacho.mx');
    await page.locator('input[type=password]').first().fill('Prueba-2026-mx');
    await page.locator('button[type=submit], form button').first().click();
    await page.waitForTimeout(2500);
  }

  return page.evaluate(() => {
    const desc = el => {
      let s = el.tagName.toLowerCase();
      if (el.className && typeof el.className === 'string') {
        s += '.' + el.className.trim().split(/\s+/).slice(0, 4).join('.');
      }
      return s.slice(0, 110);
    };
    const texto = el => (el.textContent || '').trim().replace(/\s+/g, ' ');

    // Solo los nodos cuyo TEXTO no puede partir. `nowrap`, `truncate` y
    // cualquier cosa con `w-` fijo son los sospechosos; el resto se puede
    // romper en palabras y nunca va a imponer un min-content grande.
    const candidatos = Array.from(document.querySelectorAll('body *')).filter(el => {
      const cs = getComputedStyle(el);
      const noParte = cs.whiteSpace === 'nowrap' || cs.whiteSpace === 'pre';
      // Solo los que tienen texto PROPIO y no puros wrappers.
      const tieneTexto = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
      return (noParte || tieneTexto) && tieneTexto;
    });

    const midir = (el) => {
      const caja = document.createElement('div');
      caja.style.cssText = 'position:absolute;left:-99999px;top:0;width:min-content';
      const clon = el.cloneNode(true);
      caja.appendChild(clon);
      document.body.appendChild(caja);
      const w = caja.getBoundingClientRect().width;
      caja.remove();
      return Math.round(w);
    };

    const out = [];
    for (const el of candidatos) {
      const mc = midir(el);
      const real = Math.round(el.getBoundingClientRect().width);
      // Solo interesan los que(IMPUGN) mas de lo que miden: ese exceso es
      // exactamente lo que estira la columna del grid.
      if (mc > real + 2 && mc > 200) {
        out.push({ sel: desc(el), real, minContent: mc, texto: texto(el).slice(0, 70) });
      }
    }
    return { scrollWidth: document.documentElement.scrollWidth, culpables: out.sort((a,b)=>b.minContent-a.minContent).slice(0, 12) };
  });
}
