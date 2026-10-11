// Validacion compartida de formularios.
//
// Los montos viajan como string porque el <input type="number"> los entrega
// asi, y el backend los declara Decimal. Cualquier conversion prematura
// pasaria por float y perderia precision en la conciliacion.

/** RFC mexicano: 3 letras (moral) o 4 (fisica) + 6 digitos (YYMMDD) + 3 alfanumericos. */
export const RFC_REGEX = /^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$/;

/** El parser devuelve esto cuando no logra identificar al emisor. */
export const UNKNOWN_PROVIDER = 'Unknown Provider';

export type TicketFormErrors = Partial<Record<
  'company_id' | 'provider_name' | 'provider_tax_id' | 'total_amount' | 'tax_amount' | 'expense_date' | 'category',
  string
>>;

export interface TicketFormValues {
  company_id: string;
  provider_name: string;
  provider_tax_id?: string;
  total_amount: string;
  tax_amount?: string;
  expense_date: string;
  category?: string;
}

const MAX_AMOUNT = 9999999999.99; // Numeric(12, 2)

/** Convierte a numero solo para validar. Devuelve null si no es utilizable. */
function parseAmount(value: string): number | null {
  const cleaned = (value ?? '').trim().replace(/[$,\s]/g, '');
  if (cleaned === '') return null;
  // Rechaza "12.5.6", "abc", "1e5" y cualquier cosa que Number() coerced
  if (!/^-?\d*\.?\d*$/.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

function isRealDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  // Rechaza 2025-02-30: Date() lo normaliza a marzo y el round-trip no cuadra
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

export function validateTicketForm(values: TicketFormValues): TicketFormErrors {
  const errors: TicketFormErrors = {};

  if (!values.company_id) {
    errors.company_id = 'Selecciona una empresa';
  }

  const name = (values.provider_name ?? '').trim();
  if (!name) {
    errors.provider_name = 'El proveedor es obligatorio';
  } else if (name === UNKNOWN_PROVIDER) {
    errors.provider_name = 'Corrige el proveedor: la extracción no lo identificó';
  } else if (name.length > 150) {
    errors.provider_name = 'Máximo 150 caracteres';
  }

  const taxId = (values.provider_tax_id ?? '').trim();
  if (taxId) {
    if (!RFC_REGEX.test(taxId.toUpperCase())) {
      errors.provider_tax_id = 'RFC inválido (formato: 3-4 letras, 6 dígitos, 3 caracteres)';
    }
  }

  const total = parseAmount(values.total_amount);
  if (total === null) {
    errors.total_amount = 'Ingresa un monto válido';
  } else if (total <= 0) {
    errors.total_amount = 'El total debe ser mayor a 0';
  } else if (total > MAX_AMOUNT) {
    errors.total_amount = 'El total excede el límite permitido';
  }

  const taxRaw = (values.tax_amount ?? '').trim();
  if (taxRaw) {
    const tax = parseAmount(taxRaw);
    if (tax === null) {
      errors.tax_amount = 'Ingresa un monto válido';
    } else if (tax < 0) {
      errors.tax_amount = 'El IVA no puede ser negativo';
    } else if (total !== null && tax > total) {
      errors.tax_amount = 'El IVA no puede ser mayor al total';
    } else if (tax > MAX_AMOUNT) {
      errors.tax_amount = 'El IVA excede el límite permitido';
    }
  }

  if (!values.expense_date) {
    errors.expense_date = 'La fecha es obligatoria';
  } else if (!isRealDate(values.expense_date)) {
    errors.expense_date = 'Fecha inválida';
  }

  if ((values.category ?? '').length > 100) {
    errors.category = 'Máximo 100 caracteres';
  }

  return errors;
}

export function hasErrors(errors: TicketFormErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** Normaliza antes de enviar: recorta espacios y sube el RFC a mayusculas. */
export function normalizeTicketForm(values: TicketFormValues): TicketFormValues {
  return {
    ...values,
    provider_name: (values.provider_name ?? '').trim(),
    provider_tax_id: (values.provider_tax_id ?? '').trim().toUpperCase() || undefined,
    category: (values.category ?? '').trim() || undefined,
  };
}
