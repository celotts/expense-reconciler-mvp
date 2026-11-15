import { useCallback, useEffect, useState } from 'react';
import { fetchApi } from '../services/api';
import { Card, Loading, Modal } from '../components/ui';

interface EventoAuditoria {
  id: number;
  usuario_id: string | null;
  usuario_email: string | null;
  rol: string | null;
  accion: string;
  modulo: string | null;
  registro_afectado: string | null;
  detalles: string | null;
  ip_inet: string | null;
  fecha: string | null;
}

interface AuditoriaRespuesta {
  total: number;
  items: EventoAuditoria[];
}

export function Auditoria() {
  const [items, setItems] = useState<EventoAuditoria[]>([]);
  const [total, setTotal] = useState(0);
  const [cargando, setCargando] = useState(true);
  const [pagina, setPagina] = useState(0);
  const porPagina = 15;

  const [filtroAccion, setFiltroAccion] = useState('');
  const [filtroModulo, setFiltroModulo] = useState('');
  const [seleccionado, setSeleccionado] = useState<EventoAuditoria | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const params = new URLSearchParams();
    if (filtroAccion) params.set('accion', filtroAccion);
    if (filtroModulo) params.set('modulo', filtroModulo);
    params.set('limit', String(porPagina));
    params.set('offset', String(pagina * porPagina));
    try {
      const r = await fetchApi<AuditoriaRespuesta>(`/auditoria?${params.toString()}`);
      setItems(r.items);
      setTotal(r.total);
    } finally {
      setCargando(false);
    }
  }, [filtroAccion, filtroModulo, pagina]);

  useEffect(() => { setPagina(0); }, [filtroAccion, filtroModulo]);
  useEffect(() => { cargar(); }, [cargar]);

  function mostrarDetalles(d: string | null) {
    if (!d) return '—';
    try {
      const o = JSON.parse(d);
      return Object.entries(o).map(([k, v]) => `${k}=${v}`).join(' · ');
    } catch {
      return d;
    }
  }

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-bold text-ink">Auditoría de la aplicación</h1>
      <div className="flex gap-2">
        <input
          placeholder="accion"
          value={filtroAccion}
          onChange={e => setFiltroAccion(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
        />
        <input
          placeholder="modulo"
          value={filtroModulo}
          onChange={e => setFiltroModulo(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
        />
        <button onClick={cargar} className="px-4 py-1.5 text-sm bg-primary-600 text-white font-bold rounded-full hover:bg-primary-700">Filtrar</button>
        <span className="text-xs text-gray-500 self-center">{total} eventos</span>
      </div>

      {cargando ? <Loading message="Cargando auditoría..." /> : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-primary-600 text-white">
                <tr>
                  <th className="p-2 text-left">Fecha</th>
                  <th className="p-2 text-left">Usuario</th>
                  <th className="p-2 text-left">Perfil</th>
                  <th className="p-2 text-left">Acción</th>
                  <th className="p-2 text-left">Módulo</th>
                  <th className="p-2 text-left">Detalles</th>
                  <th className="p-2 text-left">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {items.map(e => (
                  <tr key={e.id} className="cursor-pointer hover:bg-primary-50" onClick={() => setSeleccionado(e)}>                    <td className="p-2 whitespace-nowrap">{e.fecha ? new Date(e.fecha).toLocaleString() : '—'}</td>
                    <td className="p-2">{e.usuario_email || e.usuario_id || '—'}</td>
                    <td className="p-2">{e.rol || '—'}</td>
                    <td className="p-2">{e.accion}</td>
                    <td className="p-2">{e.modulo || '—'}</td>
                    <td className="p-2 max-w-xs truncate" title={e.detalles || undefined}>{mostrarDetalles(e.detalles)}</td>
                    <td className="p-2">{e.ip_inet || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between px-4 py-2 text-sm">
            <span className="text-xs text-gray-500">
              {(total > 0) ? `${pagina * porPagina + 1}-${Math.min(total, (pagina + 1) * porPagina)} de ${total}` : '0 eventos'}
            </span>
            <div className="flex gap-2">
              <button
                disabled={pagina === 0 || cargando}
                onClick={() => setPagina(p => Math.max(0, p - 1))}
                className="px-3 py-1 text-xs border rounded disabled:opacity-40 hover:bg-primary-50 hover:text-primary-800"
              >
                Anterior
              </button>
              <button
                disabled={(pagina + 1) * porPagina >= total || cargando}
                onClick={() => setPagina(p => p + 1)}
                className="px-3 py-1 text-xs border rounded disabled:opacity-40 hover:bg-primary-50 hover:text-primary-800"
              >
                Siguiente
              </button>
            </div>
          </div>
        </Card>
      )}

      <Modal isOpen={seleccionado !== null} onClose={() => setSeleccionado(null)} title="Detalle del evento de auditoría">
        {seleccionado && (
          <div className="space-y-2 text-sm">
            <p><strong>ID:</strong> {seleccionado.id}</p>
            <p><strong>Fecha:</strong> {seleccionado.fecha ? new Date(seleccionado.fecha).toLocaleString() : '—'}</p>
            <p><strong>Usuario:</strong> {seleccionado.usuario_email || seleccionado.usuario_id || '—'}</p>
            <p><strong>Perfil:</strong> {seleccionado.rol || '—'}</p>
            <p><strong>Acción:</strong> {seleccionado.accion}</p>
            <p><strong>Módulo:</strong> {seleccionado.modulo || '—'}</p>
            <p><strong>Registro:</strong> {seleccionado.registro_afectado || '—'}</p>
            <p><strong>IP:</strong> {seleccionado.ip_inet || '—'}</p>
            <p><strong>Detalles (JSON):</strong></p>
            <pre className="bg-primary-50 rounded p-2 text-xs overflow-auto max-h-60 whitespace-pre-wrap">
              {seleccionado.detalles ? JSON.stringify(JSON.parse(seleccionado.detalles), null, 2) : '—'}
            </pre>
          </div>
        )}
      </Modal>
    </div>
  );
}
