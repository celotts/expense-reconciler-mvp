# Expense Reconciler — Guía para el usuario

Herramienta para freelancers y pequeños comercios mexicanos: registra comprobantes,
los concilia contra el extracto bancario y exporta a Excel/CONTPAQI.

## Requisitos
- Docker Desktop (Windows/Mac/Linux)
- 4 GB de RAM libres
- 5 GB de disco

## Instalación (una vez)
1. Descarga el archivo `expense-reconciler.zip` y descomprímelo
2. Abre una terminal en la carpeta descomprimida
3. Ejecuta: `bash setup.sh`
4. Espera a que termine (la primera vez descarga ~2 GB)
5. Abre http://localhost:3000 en tu navegador

## Primer uso
1. Ingresa con el `email` configurado en `.env` y la contraseña de `password_inicial` (por defecto: `admin@localhost.com`)
2. **Cambia la contraseña** en la sección de usuarios
3. Crea tu empresa (nombre y RFC)
4. Sube tu primer comprobante (foto o PDF)
5. Revisa los datos que el sistema leyó
6. Concilia contra tu extracto bancario
7. Descarga el Excel para CONTPAQI

## Cómo usarlo día a día

### Registrar un comprobante
- **Opción A (automática)**: Sube la foto/PDF. El sistema lee los datos automáticamente. Revisa y corrige si es necesario.
- **Opción B (manual)**: Teclea los datos a mano. El sistema no lee nada, solo guarda lo que escribiste.

### Conciliar con el banco
1. Sube tu extracto bancario (CSV o Excel del banco)
2. El sistema cruza los montos automáticamente
3. Revisa los que no cuadran (marcados como MANUAL o DISCREPANCY)
4. Ajusta y vuelve a ejecutar

### Cierre mensual
1. Ve a la sección de Reportes
2. Selecciona el mes
3. El sistema te dice si el periodo se puede declarar cerrado
4. Descarga el PDF firmable

## Preguntas frecuentes

**¿Mis datos salen de mi máquina?**
No. Todo corre localmente en Docker. Los comprobantes, los datos y la IA (Ollama) están en tu máquina.

**¿Necesito internet?**
Solo para la instalación inicial. Después funciona sin conexión.

**¿Qué formatos de comprobante acepta?**
PDF, JPG, PNG, HEIC (fotos de iPhone), TIFF.

**¿Funciona con CONTPAQI?**
Sí. Exporta a Excel con el formato que CONTPAQI espera.

**¿Puedo tener varias empresas?**
Sí. Cada empresa tiene su propia configuración, comprobantes y conciliaciones.

## Soporte
- WhatsApp: [tu número]
- Email: [tu email]
- Horario: Lunes a Viernes, 9:00 - 18:00 (hora CDMX)