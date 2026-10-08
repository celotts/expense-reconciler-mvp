// Entra con la cuenta de prueba y deja el navegador en el Inicio.
// Lo usan los scripts de medicion que necesitan sesion y no quieren repetir el
// login en cada uno.
export default async function run(page, ui) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  const correo = page.locator('input[type=email]').first();
  const pass = page.locator('input[type=password]').first();
  if (await pass.count() === 0) return { yaDentro: true };

  await correo.fill('prueba@despacho.mx');
  await pass.fill('Prueba-2026-mx');
  await page.locator('button[type=submit], form button').first().click();
  await page.waitForTimeout(2500);

  return {
    yaDentro: (await page.locator('input[type=password]').count()) === 0,
    texto: (await page.locator('body').innerText()).slice(0, 120).replace(/\s+/g, ' '),
  };
}
