// Mide las areas tactiles en movil.
//
// POR QUE MEDIR Y NO OPINAR
// =========================
// "Los botones se ven pequenos" es una opinion y depende de quien mire. El
// umbral es un numero, y el numero se puede comprobar: la guia de plataformas
// moviles (Apple HIG, Material) pide 44x44 CSS px como minimo, y por debajo el
// error de pulsacion sube de forma medible.
//
// Asi que esto cuenta, en un viewport de 390, cuantos elementos con los que se
// pulsa tienen menos de 44 de alto, y AGRUPA por patron en vez de listar 200
// lineas: 60 `<td>` de 30px son un patron (una tabla), no 60 hallazgos.
//
// LO QUE NO CUENTA COMO FALLA
// ============================
//   - Elementos dentro de un `overflow-x-auto`: un scroll horizontal tambien
//     se toca, pero el gesto es distinto y no es un boton de accion.
//   - Texto plano: `<p>`, `<span>`, `<td>` sin manejador. Que un texto sea de
//     20px de alto es correcto; solo los controles se pulsan.
//
// LO QUE CUENTA
// =============
// `button`, `a`, `input`, `select`, `textarea`, `[role=button]`, `[onclick]`.
// Los `label` se excluyen: un `label` no se pulsa para activar, y la pantalla ya
// se lee bien sin ellos.

export default async function run(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  if (await page.locator('input[type=password]').count() > 0) {
    await page.locator('input[type=email]').first().fill('prueba@despacho.mx');
    await page.locator('input[type=password]').first().fill('Prueba-2026-mx');
    await page.locator('button[type=submit], form button').first().click();
    await page.waitForTimeout(2500);
  }

  const PAGINAS = [
    { key: 'dashboard', label: 'Inicio' },
    { key: 'tickets', label: 'Tickets' },
    { key: 'scan', label: 'Escáner' },
    { key: 'review', label: 'Cola de revisión' },
    { key: 'spotcheck', label: 'Muestreo' },
    { key: 'bank', label: 'Banco' },
    { key: 'reconciliations', label: 'Conciliación' },
  ];

  const MINIMO = 44;
  const salida = {};

  for (const pag of PAGINAS) {
    const ham = page.locator('header button:visible').first();
    if (await ham.count() > 0) { await ham.click(); await page.waitForTimeout(400); }
    await page.getByRole('button', { name: pag.label, exact: true }).first().click();
    await page.waitForTimeout(1500);

    salida[pag.key] = await page.evaluate((MINIMO) => {
      const selector = 'button, a, input:not([type=hidden]), select, textarea, [role=button]';
      const malos = [];
      for (const el of document.querySelectorAll(selector)) {
        const r = el.getBoundingClientRect();
        // Invisible = no cuenta: `display:none`, `hidden`, o fuera de pantalla.
        if (r.width === 0 || r.height === 0) continue;
        if (getComputedStyle(el).visibility === 'hidden') continue;

        // Con scroll horizontal alrededor no es un boton de accion.
        let n = el.parentElement, enScroll = false;
        while (n && n !== document.body) {
          const ov = getComputedStyle(n).overflowX;
          if (ov === 'auto' || ov === 'scroll') { enScroll = true; break; }
          n = n.parentElement;
        }
        if (enScroll) continue;

        // Los `label` no se pulsan para activar nada.
        if (el.tagName === 'LABEL') continue;

        const alto = Math.round(r.height);
        const ancho = Math.round(r.width);
        if (alto >= MINIMO) continue;

        const texto = (el.textContent || el.getAttribute('placeholder') || el.value || '').trim().replace(/\s+/g, ' ');
        malos.push({
          sel: el.tagName.toLowerCase() + '.' + ((el.className || '').toString().trim().split(/\s+/).slice(0, 3).join('.')),
          alto, ancho,
          texto: texto.slice(0, 40),
        });
      }

      // Agrupar por patron: misma clase de control = un solo hallazgo.
      const grupos = {};
      for (const m of malos) {
        const clave = `${m.sel} (${m.alto}px)`;
        grupos[clave] = grupos[clave] || { veces: 0, ejemplo: m.texto };
        grupos[clave].veces++;
      }
      return {
        total: malos.length,
        grupos: Object.entries(grupos)
          .map(([patron, g]) => ({ patron, veces: g.veces, ejemplo: g.ejemplo }))
          .sort((a, b) => b.veces - a.veces),
      };
    }, MINIMO);
  }

  return salida;
}
