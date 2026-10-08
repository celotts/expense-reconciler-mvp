// Mide las filas de "Principales proveedores" una por una, pieza por pieza.
//
// POR QUE UN SONDEO MAS Y POR QUE ESTE
// ====================================
// Los tres scripts anteriores ya bajaron por el arbol y ninguno llego aqui: el
// ultimo termino en la tarjeta y no en la pieza, porque las hojas —los `span` de
// texto— dan un min-content pequeno y la tarjeta da 403. Faltaba el nivel de
// EN MEDIO: los hijos directos de la fila, que son flex items y pueden imponer
// ancho aunque su texto sea corto.
//
// Sigue siendo mas util MEJOR: mide cada pieza de la fila por separado y
// dice cuanto impone cada una. Con eso no hay que deducir nada: se suma lo que
// dicen las piezas y se compara con el ancho real de la fila.
//
// `imponen` es el dato: min-content MENOS el ancho que realmente tiene. Una
// pieza con `imponen = 0` no estira nada por su cuenta; una con `imponen = 163`
// es la culpable, y el arreglo es suyo.

export default async function run(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  if (await page.locator('input[type=password]').count() > 0) {
    await page.locator('input[type=email]').first().fill('prueba@despacho.mx');
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
      return s.slice(0, 95);
    };
    const midir = el => {
      const caja = document.createElement('div');
      caja.style.cssText = 'position:absolute;left:-99999px;top:0;width:min-content';
      caja.appendChild(el.cloneNode(true));
      document.body.appendChild(caja);
      const w = caja.getBoundingClientRect().width;
      caja.remove();
      return Math.round(w);
    };

    // La tarjeta de proveedores: la que tiene el h2 con ese texto.
    const h2 = Array.from(document.querySelectorAll('h2'))
      .find(h => (h.textContent || '').includes('Principales proveedores'));
    if (!h2) return { error: 'no se encontro la tarjeta de proveedores' };
    const card = h2.closest('div');
    const filas = Array.from(card.querySelectorAll('div.flex.items-center'));

    return {
      tarjeta: {
        minContent: midir(card),
        real: Math.round(card.getBoundingClientRect().width),
        padding: getComputedStyle(card).padding,
      },
      filas: filas.slice(0, 3).map(fila => ({
        minContent: midir(fila),
        real: Math.round(fila.getBoundingClientRect().width),
        gap: getComputedStyle(fila).gap,
        piezas: Array.from(fila.children).map(p => ({
          sel: desc(p),
          minContent: midir(p),
          real: Math.round(p.getBoundingClientRect().width),
          // `imponen` = lo que ESTIRA. Un `truncate` con `flex-1` deberia dar 0
          // porque `overflow:hidden` pone su minimo automatico en 0; si aqui sale
          // un numero grande, esa regla no esta aplicando y hay que decirlo.
          imponen: midir(p) - Math.round(p.getBoundingClientRect().width),
          texto: (p.textContent || '').trim().slice(0, 30),
        })),
      })),
    };
  });
}
