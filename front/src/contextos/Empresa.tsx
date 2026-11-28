/** La empresa activa, una sola para toda la aplicacion.
 *
 *  Antes cada pantalla tenia su propio `useState(companyId)` y llamaba a
 *  `companiesApi.list()`. Eso respondia "qué empresas existen", no "a cuáles
 *  tiene acceso esta cuenta", y permitia que Escáner y Tickets defendieran
 *  empresas distintas con el mismo token. Ahora la lista viene de
 *  `GET /auth/empresas` y la eleccion se manda en un unico sitio (`api.ts`).
 *
 *  El rol de aqui es solo para NO ofrecer acciones imposibles en la interfaz.
 *  No autoriza nada: el servidor lo relee de la BD en cada peticion. Un front
 *  que se fiara de su propia copia del rol se quedaria creyendo que alguien
 *  sigue siendo admin despues de que le cambiaran el rol.
 */
import { useCallback, useEffect, useMemo, useState, ReactNode } from 'react';
import { empresaActivaGuardada, fetchApi, fijarEmpresaActiva } from '../services/api';
import { useSesion } from './SesionContexto';
import {
  EmpresaContext, type EmpresaContextValue, type MembresiaEmpresa, useEmpresa,
} from './EmpresaContexto';

export function EmpresaProvider({ children }: { children: ReactNode }) {
  const { usuario } = useSesion();
  const userId = usuario?.id;
  const [empresas, setEmpresas] = useState<MembresiaEmpresa[]>([]);
  const [elegida, setElegida] = useState<string | null>(() => empresaActivaGuardada());
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const recargar = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    if (!userId) {
      setEmpresas([]);
      setError(null);
      fijarEmpresaActiva(null);
      return;
    }

    let vivo = true;
    setCargando(true);
    fetchApi<MembresiaEmpresa[]>('/auth/empresas')
      .then((lista) => {
        if (!vivo) return;
        setEmpresas(lista);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!vivo) return;
        // Un fallo de red no puede borrar la empresa elegida: la ultima lista
        // valida sigue siendo mejor que una pantalla sin empresa y sin motivo.
        // Borrar `empresas` aqui deria la empresa derivada a null y el efecto
        // de abajo quitaria la cabecera, asi que el comentario mentiria.
        setError(e instanceof Error ? e.message : 'No se pudieron cargar las empresas');
      })
      .finally(() => {
        if (vivo) setCargando(false);
      });

    return () => {
      vivo = false;
    };
  }, [userId, version]);

  // La memoria del modulo y el localStorage siguen a la eleccion derivada, no
  // al `useState`: si la cuenta solo tiene una empresa, esa empresa queda
  // activa aunque nadie haya pulsado nada.
  const empresaActiva = useMemo(
    () =>
      empresas.find((m) => m.company_id === elegida)
      ?? (empresas.length === 1 ? empresas[0] : null),
    [empresas, elegida],
  );

  useEffect(() => {
    fijarEmpresaActiva(empresaActiva?.company_id ?? null);
  }, [empresaActiva?.company_id]);

  const elegirEmpresa = useCallback((companyId: string) => {
    setElegida(companyId);
    fijarEmpresaActiva(companyId);
  }, []);

  const valor = useMemo<EmpresaContextValue>(
    () => ({
      empresas,
      empresaActiva,
      rol: empresaActiva?.rol ?? null,
      permisos: empresaActiva?.permisos ?? [],
      cargando,
      necesitaEleccion: !cargando && empresas.length > 1 && empresaActiva === null,
      error,
      elegirEmpresa,
      recargar,
    }),
    [empresas, empresaActiva, cargando, error, elegirEmpresa, recargar],
  );

  return <EmpresaContext.Provider value={valor}>{children}</EmpresaContext.Provider>;
}

/** El mismo aviso en las tres pantallas que escriben datos de empresa.
 *
 *  No es un `if` copiado tres veces: un aviso que solo existe en Escáner se
 *  queda sin decir nada en Tickets, y cada copia puede cambiar de texto. Se
 *  monta con `renderToStaticMarkup` en el verificador, asi que lo que se prueba
 *  es el HTML y no el retorno de una funcion.
 */
export function AvisoSinEmpresa({ mensaje }: { mensaje?: string }) {
  const { empresaActiva, cargando } = useEmpresa();
  if (cargando || empresaActiva?.estado === 'activo') return null;

  if (empresaActiva?.estado === 'inactivo') {
    const motivo = empresaActiva.motivo_baja
      ? ` Motivo declarado: ${empresaActiva.motivo_baja}.`
      : '';
    return (
      <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        La empresa {empresaActiva.name} esta anulada. No se puede escanear ni procesar informacion; solo consultar sus datos. Actívela para continuar.{motivo}
      </div>
    );
  }

  return (
    <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      {mensaje ?? 'No hay ninguna empresa activa. Active o cree una en la barra lateral antes de procesar gastos.'}
    </div>
  );
}

/** La empresa a la que se atribuye la corrida, visible ANTES de pulsar.
 *
 *  No es un segundo sitio donde se decide: el `<select>` llama al MISMO
 *  `elegirEmpresa` del contexto, que es el unico que escribe la cabecera
 *  `X-Company-ID` (`fijarEmpresaActiva`). Un `company_id` suelto en el cuerpo
 *  seria una segunda verdad, y el servidor lo rechazaria con 409 sin que el
 *  operador supiera cual de las dos gana.
 *
 *  Si no hay empresa, o la elegida no esta activa, aqui no se pinta nada:
 *  `AvisoSinEmpresa` ya dice POR QUE. Dos carteles compitiendo por el mismo
 *  estado seria el mismo error que estamos evitando.
 *
 *  Las sucursales no se representan como empresas: empresa es la entidad
 *  fiscal (RFC), y todos sus comprobantes se consolidan en la misma corrida.
 */
export function CartelEmpresaEnDigitalizacion() {
  const { empresas, empresaActiva, cargando, elegirEmpresa } = useEmpresa();
  if (cargando || empresaActiva?.estado !== 'activo') return null;

  const activas = empresas.filter((m) => m.estado === 'activo');
  return (
    <div
      className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900"
      data-empresa-digitalizacion={empresaActiva.company_id}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <strong>{`Digitalizando en: ${empresaActiva.name}`}</strong>
        <span className="text-xs">{empresaActiva.tax_id}</span>
        {activas.length > 1 && (
          <label className="flex items-center gap-2 text-xs">
            <span>Cambiar</span>
            <select
              aria-label="Empresa para digitalizar"
              value={empresaActiva.company_id}
              onChange={e => elegirEmpresa(e.target.value)}
              className="px-2 py-1 text-sm border border-blue-300 rounded-lg bg-white"
            >
              {activas.map((m) => (
                <option key={m.company_id} value={m.company_id}>{m.name}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="mt-1 text-xs text-blue-800">
        Toda la corrida se atribuye a esta entidad fiscal. Una sucursal no es otra
        empresa: comparte este RFC y sus comprobantes se consolidan aqui.
      </p>
    </div>
  );
}
