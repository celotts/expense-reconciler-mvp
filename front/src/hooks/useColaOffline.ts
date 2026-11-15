/**
 * Hook de React para la cola offline.
 *
 * Solo existe para que los componentes reciban estado; toda la lógica está
 * en `services/colaOffline.ts` y los avisos viajan por el documento con el
 * evento `gastos-offline:cambio` — ningún módulo importa vistas.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  EVENTO_GASTOS_OFFLINE,
  eliminarPendiente,
  listarPendientes,
  sincronizarPendientes,
  type GastoPendiente,
  type ResumenSincronizacion,
} from '../services/colaOffline';

export function useColaOffline(seed?: GastoPendiente[]) {
  // `seed` es una costura de prueba (mismo criterio que `informeInicial` en
  // Cierre.tsx): con ella, el verificador monta el componente sin IndexedDB.
  // Sin `seed`, el comportamiento de produccion no cambia.
  const [pendientes, setPendientes] = useState<GastoPendiente[]>(seed ?? []);
  const [sincronizando, setSincronizando] = useState(false);
  const [ultimoResumen, setUltimoResumen] = useState<ResumenSincronizacion | null>(null);

  const refrescar = useCallback(async () => {
    if (seed) {
      setPendientes(seed);
      return;
    }
    try {
      setPendientes(await listarPendientes());
    } catch {
      // Sin IndexedDB no hay cola: la pantalla normal muestra "0".
      setPendientes([]);
    }
  }, [seed]);

  useEffect(() => {
    void refrescar();

    const alCambio = () => {
      void refrescar();
    };
    document.addEventListener(EVENTO_GASTOS_OFFLINE, alCambio);
    window.addEventListener('online', alCambio);
    window.addEventListener('offline', alCambio);

    return () => {
      document.removeEventListener(EVENTO_GASTOS_OFFLINE, alCambio);
      window.removeEventListener('online', alCambio);
      window.removeEventListener('offline', alCambio);
    };
  }, [refrescar]);

  const sincronizar = useCallback(async () => {
    setSincronizando(true);
    try {
      const resumen = await sincronizarPendientes();
      setUltimoResumen(resumen);
      await refrescar();
      return resumen;
    } finally {
      setSincronizando(false);
    }
  }, [refrescar]);

  const descartar = useCallback(
    async (id: string) => {
      await eliminarPendiente(id);
      await refrescar();
    },
    [refrescar],
  );

  return {
    pendientes,
    cantidad: pendientes.length,
    sincronizando,
    ultimoResumen,
    sincronizar,
    descartar,
  };
}
