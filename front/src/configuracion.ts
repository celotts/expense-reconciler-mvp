export interface ConfigApp {
  locale: string;
  currency: string;
  /** Opciones de `Intl.DateTimeFormat`, no un diccionario libre: el objeto
   *  se reparte directamente al formateador y un `as any` aqui esconderia una
   *  opcion mal escrita hasta que el navegador la ignorara en silencio. */
  date: Intl.DateTimeFormatOptions;
  dateTime: Intl.DateTimeFormatOptions;
}

const defaultConfig: ConfigApp = {
  locale: 'es-MX',
  currency: 'MXN',
  date: { day: '2-digit', month: '2-digit', year: 'numeric' },
  dateTime: { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' },
};

let config: ConfigApp = defaultConfig;

export function getConfig(): ConfigApp {
  return config;
}

export async function loadConfig(): Promise<void> {
  try {
    const r = await fetch('/app-config.json', { cache: 'no-store' });
    if (!r.ok) return;
    const data = (await r.json()) as ConfigApp;
    config = { ...defaultConfig, ...data };
  } catch {
    // fallback default
  }
}