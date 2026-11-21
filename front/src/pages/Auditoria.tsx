import { useCallback, useEffect, useState } from 'react';
import { fetchApi } from '../services/api';
import { Card, Loading, Modal } from '../components/ui';

interface EventoAuditoria {
  id: number;
  usuario_id: string | null;
  usuario_email: string | null;
  usuario_nombre?: string | null;
  rol: string | null;
  accion: string;
  modulo: string | null;
  registro_afectado: string | null;
  detalles: string | null;
  ip_inet: string | null;
  fecha: string | null;
  resultado?: string | null;
}

const MODULOS: Record<string, string> = {
  auth: 'Acceso', tickets: 'Tickets', ticket: 'Tickets', companies: 'Empresas',
  empresas: 'Empresas', users: 'Usuarios', usuarios: 'Usuarios', scan: 'Escaneo',
  auditoria: 'Auditoría', lotes: 'Lotes', reconciliations: 'Conciliaciones',
  bank_transactions: 'Banco', cierres: 'Cierre', productos: 'Productos',
  tipos_gasto: 'Tipos de gasto',
};

const ACCIONES: Record<string, string> = {
  login: 'Inicio de sesión', logout: 'Cierre de sesión',
  login_fallido: 'Inicio de sesión fallido', login_fallido_fallido: 'Inicio de sesión fallido',
  crear_ticket: 'Creó ticket', crear_empresa: 'Creó empresa', crear_usuario: 'Creó usuario',
  actualizar_ticket: 'Actualizó ticket', actualizar_empresa: 'Actualizó empresa',
  actualizar_usuario: 'Actualizó usuario', delete: 'Eliminó registro',
  clasificar: 'Clasificó ticket', veredicto: 'Veredicto de ticket',
  digitalizar: 'Digitalizó ticket', limpiar_base: 'Limpió base de escaneos',
  cambiar_contrasena: 'Cambió contraseña', cambiar_perfil: 'Cambió perfil',
  crear: 'Creó registro', update: 'Actualizó registro', error_sistema: 'Error del sistema',
};

function textoAccion(e: EventoAuditoria): string {
  const clave = `${e.accion}|${e.modulo || ''}`;
  if (ACCIONES[clave]) return ACCIONES[clave];
  if (ACCIONES[e.accion]) {
    const base = ACCIONES[e.accion];
    if (e.accion === 'update' || e.accion === 'delete' || e.accion === 'crear') {
      const m = MODULOS[e.modulo || ''] || '';
      if (m) return base.replace('registro', m.replace(/s$/, '').toLowerCase());
    }
    return base;
  }
  const a = e.accion.replace(/_fallido$/, ' fallido');
  if (a.startsWith('crear ')) return `Creó ${a.slice(6)}`;
  if (a.startsWith('actualizar ')) return `Actualizó ${a.slice(11)}`;
  if (a.startsWith('eliminar ')) return `Eliminó ${a.slice(9)}`;
  return a.charAt(0).toUpperCase() + a.slice(1);
}

function textoActor(e: EventoAuditoria): string {
  // El actor es el usuario ya logueado en esa sesión: nombre y email, con rol.
  const nombre = e.usuario_nombre || e.usuario_email || 'Usuario desconocido';
  return e.rol ? `${nombre} (${e.rol})` : nombre;
}

function textoDetalle(e: EventoAuditoria): string {
  let d: Record<string, unknown> = {};
  try { d = e.detalles ? JSON.parse(e.detalles) : {}; } catch { return e.detalles || '—'; }
  // La frase empieza por el actor: "María López (contador) creó el ticket...".
  const actor = textoActor(e);
  const conActor = (frase: string) => `${actor} ${frase.charAt(0).toLowerCase()}${frase.slice(1)}`;
  const nombre = [d.nombre, d.provider_name, d.empresa, d.proveedor, d.usuario, d.identificacion].find(v => typeof v === 'string' && v) as string | undefined;
  const email = typeof d.email === 'string' ? d.email : undefined;
  const total = (d.total_amount ?? d.total ?? d.monto ?? d.importe) as number | string | undefined;
  const fecha = (d.expense_date ?? d.fecha) as string | undefined;
  const rfc = (d.provider_tax_id ?? d.rfc ?? d.tax_id) as string | undefined;
  const folio = (d.folio ?? d.numero ?? d.invoice_number) as string | undefined;
  const datos = [nombre, total !== undefined ? `$${total}` : '', fecha ? `fecha ${fecha}` : '', rfc ? `RFC ${rfc}` : '', folio ? `folio ${folio}` : '', email && email !== nombre ? email : ''].filter(Boolean).join(', ');
  if (typeof d.descripcion === 'string' && d.descripcion) return conActor(d.descripcion);
  if (e.accion === 'login') return conActor(`Inició sesión correctamente${e.ip_inet ? ` desde ${e.ip_inet}` : ''}`);
  if (e.accion === 'logout') return conActor(`Cerró sesión${e.ip_inet ? ` desde ${e.ip_inet}` : ''}`);
  if (e.accion.includes('fallido')) return `${actor} intentó iniciar sesión${nombre ? ` con ${nombre}` : ''}${d.motivo ? `: ${d.motivo}` : ''}${e.ip_inet ? ` desde ${e.ip_inet}` : ''}`;
  if (Array.isArray(d.cambios) && d.cambios.length) {
    const CAMPOS: Record<string, string> = { total_amount: 'total', provider_name: 'proveedor', expense_date: 'fecha', provider_tax_id: 'RFC', name: 'nombre', tax_id: 'RFC', nombre: 'nombre', email: 'correo', perfil: 'perfil', grupo: 'grupo', is_active: 'activo', plan: 'plan' };
    const lista = (d.cambios as Array<Record<string, unknown>>).map(c => `${CAMPOS[String(c.campo)] || String(c.campo)}: ${c.antes} → ${c.despues}`).join('; ');
    return conActor(`Actualizó ${datos ? `${datos}: ` : ''}${lista}`);
  }
  if (e.accion === 'cambiar_perfil') return conActor(`Cambió el perfil${nombre ? ` de ${nombre}` : ''} de ${d.perfil_anterior || '?'} a ${d.perfil_nuevo || '?'}`);
  if (e.accion === 'cambiar_contrasena') return conActor(`Cambió la contraseña${nombre || email ? ` de ${nombre || email}` : ''}`);
  if (e.accion === 'limpiar_base') {
    const b = d.borrado as Record<string, unknown> | undefined;
    const n = b ? Object.entries(b).filter(([, v]) => typeof v === 'number' && (v as number) > 0).map(([k, v]) => `${v} ${k}`).join(', ') : '';
    return conActor(`Limpió la base de escaneos${n ? `: borró ${n}` : ''}`);
  }
  if (e.accion === 'clasificar') return conActor(`Clasificó ${d.actualizados ?? d.pedidos ?? ''} tickets en la categoría ${d.category || 'sin categoría'}`);
  if (e.accion === 'veredicto') return conActor(`Dio veredicto ${d.veredicto || ''} al ticket${datos ? ` de ${datos}` : ''}${Array.isArray(d.campos_incorrectos) && d.campos_incorrectos.length ? `, campos incorrectos: ${d.campos_incorrectos.join(', ')}` : ''}`);
  if (typeof d.archivo === 'string' && d.archivo) return conActor(`${textoAccion(e)}: ${d.archivo}${datos ? ` (${datos})` : ''}`);
  return conActor(`${textoAccion(e)}${datos ? `: ${datos}` : ''}`);
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

  function mostrarDetalles(e: EventoAuditoria) {
    return textoDetalle(e);
  }

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-bold">Auditoría de la aplicación</h1>
      <div className="flex gap-2">
        <input
          placeholder="accion"
          value={filtroAccion}
          onChange={e => setFiltroAccion(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
        />
        <input
          placeholder="modulo"
          value={filtroModulo}
          onChange={e => setFiltroModulo(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
        />
        <button onClick={cargar} className="px-3 py-1 text-sm bg-blue-600 text-white rounded">Filtrar</button>
        <span className="text-xs text-gray-500 self-center">{total} eventos</span>
      </div>

      {cargando ? <Loading message="Cargando auditoría..." /> : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-blue-100 text-blue-900 border-b-2 border-blue-200">
                <tr>
                  <th className="p-2 text-left">Fecha</th>
                  <th className="p-2 text-left">Usuario</th>
                  <th className="p-2 text-left">Perfil</th>
                  <th className="p-2 text-left">Acción</th>
                  <th className="p-2 text-left">Módulo</th>
                  <th className="p-2 text-left">Qué hizo</th>
                  <th className="p-2 text-left">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {items.map(e => (
                  <tr key={e.id} className="cursor-pointer hover:bg-blue-50" onClick={() => setSeleccionado(e)}>                    <td className="p-2 whitespace-nowrap">{e.fecha ? new Date(e.fecha).toLocaleString() : '—'}</td>
                    <td className="p-2"><div className="font-semibold">{e.usuario_nombre || e.usuario_email || '—'}</div><div className="text-gray-500">{e.usuario_nombre ? (e.usuario_email || '') : (e.usuario_id || '')}</div></td>
                    <td className="p-2">{e.rol || '—'}</td>
                    <td className="p-2">{textoAccion(e)}</td>
                    <td className="p-2">{MODULOS[e.modulo || ''] || e.modulo || '—'}</td>
                    <td className="p-2 max-w-xs truncate" title={mostrarDetalles(e)}>{mostrarDetalles(e)}</td>
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
                className="px-3 py-1 text-xs border rounded disabled:opacity-40"
              >
                Anterior
              </button>
              <button
                disabled={(pagina + 1) * porPagina >= total || cargando}
                onClick={() => setPagina(p => p + 1)}
                className="px-3 py-1 text-xs border rounded disabled:opacity-40"
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
            <p><strong>Usuario:</strong> {textoActor(seleccionado)} — {seleccionado.usuario_email || seleccionado.usuario_id || '—'}</p>
            <p><strong>Perfil:</strong> {seleccionado.rol || '—'}</p>
            <p><strong>Acción:</strong> {textoAccion(seleccionado)}</p>
            <p><strong>Módulo:</strong> {MODULOS[seleccionado.modulo || ''] || seleccionado.modulo || '—'}</p>
            <p><strong>Qué hizo:</strong> {textoDetalle(seleccionado)}</p>
            <p><strong>Registro:</strong> {seleccionado.registro_afectado || '—'}</p>
            <p><strong>IP:</strong> {seleccionado.ip_inet || '—'}</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
