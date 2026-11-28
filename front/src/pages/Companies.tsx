import { useState, useEffect } from 'react';
import { companiesApi, type Company, type CompanyCreate } from '../services/api';
import { 
  Button, Input, Modal, Table, Card, Loading, EmptyState 
} from '../components/ui';
import { useEmpresa, usePermisos } from '../contextos/EmpresaContexto';

const ETIQUETA_ESTADO: Record<Company['estado'], string> = {
  activo: 'Activa',
  // "Anular" es la accion y "anulada" el estado en la pantalla. El valor de la
  // base sigue siendo `inactivo`: no necesitamos otra migration para renombrar
  // una etiqueta, y el contrato HTTP no cambia.
  inactivo: 'Anulada (se puede reactivar)',
  eliminado: 'Eliminada',
};

export function Companies() {
  const { puede } = usePermisos();
  const { recargar } = useEmpresa();
  const [incluirInactivas, setIncluirInactivas] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingCompany, setEditingCompany] = useState<Company | null>(null);
  const [anulando, setAnulando] = useState<Company | null>(null);
  const [motivoBaja, setMotivoBaja] = useState('');
  const [formData, setFormData] = useState<CompanyCreate>({ name: '', tax_id: '' });
  const [submitting, setSubmitting] = useState(false);

  const loadCompanies = async () => {
    try {
      setLoading(true);
      const data = await companiesApi.list(incluirInactivas);
      setCompanies(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar empresas');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadCompanies(); }, [incluirInactivas]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      if (editingCompany) {
        await companiesApi.update(editingCompany.id, formData);
      } else {
        await companiesApi.create(formData);
      }
      setShowModal(false);
      setFormData({ name: '', tax_id: '' });
      setEditingCompany(null);
      loadCompanies();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al guardar');
    } finally {
      setSubmitting(false);
    }
  };

  const openCreateModal = () => {
    setFormData({ name: '', tax_id: '' });
    setEditingCompany(null);
    setShowModal(true);
  };

  const openEditModal = (company: Company) => {
    setFormData({ name: company.name, tax_id: company.tax_id });
    setEditingCompany(company);
    setShowModal(true);
  };

  const trasCambiar = () => {
    void loadCompanies();
    // La barra lateral decide con `/auth/empresas`: sin recargarla, una empresa
    // reactivada seguiria sin aparecer como opcion en el resto de la pantalla.
    recargar?.();
  };

  const abrirAnular = (company: Company) => {
    setAnulando(company);
    setMotivoBaja('');
  };

  const confirmarAnular = async () => {
    if (!anulando) return;
    try {
      await companiesApi.desactivar(anulando.id, motivoBaja.trim() || undefined);
      setAnulando(null);
      trasCambiar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al anular');
    }
  };

  const handleActivar = async (id: string) => {
    try {
      await companiesApi.activar(id);
      trasCambiar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al activar');
    }
  };

  const handleEliminar = async (id: string) => {
    // Soft delete: no hay confirmacion de "se borran los tickets" porque eso
    // seria prometer algo que el servidor no hace. Solo empresas sin datos.
    if (!confirm('¿Eliminar esta empresa? Solo se permite si no tiene datos; la fila se conserva como eliminada. Para conservar la evidencia, desactívela.')) return;
    try {
      await companiesApi.eliminar(id);
      trasCambiar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al eliminar');
    }
  };

  const columns = [
    {
      key: 'name', header: 'Nombre',
      render: (c: Company) => (
        <div>
          <span className="font-medium">{c.name}</span>
          {c.estado === 'inactivo' && c.motivo_baja && (
            <div className="text-xs text-gray-600">Motivo de la anulación: {c.motivo_baja}</div>
          )}
          {c.estado === 'inactivo' && c.bajada_por && (
            <div className="text-xs text-gray-400">Anulada por {c.bajada_por}</div>
          )}
        </div>
      ),
    },
    { key: 'tax_id', header: 'RFC', render: (c: Company) => <code className="text-sm bg-gray-100 px-2 py-1 rounded">{c.tax_id}</code> },
    { key: 'created_at', header: 'Creada', render: (c: Company) => new Date(c.created_at).toLocaleDateString('es-MX') },
    {
      key: 'estado', header: 'Estado',
      render: (c: Company) => (
        <span className={c.estado === 'activo' ? 'text-green-700' : 'text-gray-500'}>
          {ETIQUETA_ESTADO[c.estado]}
        </span>
      ),
    },
    {
      key: 'actions', header: 'Acciones',
      render: (c: Company) => puede('administrar') ? (
        <div className="flex gap-2">
          <Button variant="edit" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(c); }}>Editar</Button>
          {c.estado === 'activo' && (
            // Con datos, "Eliminar" seria una promesa que el servidor rechaza:
            // se ofrece "Anular", que conserva la evidencia y se puede revertir.
            c.puede_eliminarse ? (
              <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); handleEliminar(c.id); }}>Eliminar</Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); abrirAnular(c); }}>Anular</Button>
            )
          )}
          {c.estado === 'inactivo' && (
            <Button variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); handleActivar(c.id); }}>Activar</Button>
          )}
          {c.estado === 'inactivo' && c.puede_eliminarse && (
            <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); handleEliminar(c.id); }}>Eliminar</Button>
          )}
        </div>
      ) : null,
    },
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Empresas</h1>
          <p className="text-gray-500">Gestiona tus empresas y perfiles fiscales</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={incluirInactivas}
              onChange={(e) => setIncluirInactivas(e.target.checked)}
            />
            Mostrar inactivas
          </label>
          {puede('administrar') && <Button onClick={openCreateModal}>Nueva Empresa</Button>}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)}>×</Button>
        </div>
      )}

      {anulando && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold">Anular {anulando.name}</h2>
            <p className="mt-1 text-sm text-gray-600">
              Sus datos se conservan; mientras este anulada solo se podran consultar. Podras reactivarla.
            </p>
            <label className="mt-4 block text-sm font-medium" htmlFor="motivo-baja">
              Motivo de la anulación (opcional)
            </label>
            <textarea
              id="motivo-baja"
              className="mt-1 w-full rounded border border-gray-300 p-2 text-sm"
              maxLength={500}
              value={motivoBaja}
              onChange={(e) => setMotivoBaja(e.target.value)}
              placeholder="¿Por que se anula?"
            />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setAnulando(null)}>Cancelar</Button>
              <Button variant="danger" onClick={confirmarAnular}>Anular</Button>
            </div>
          </div>
        </div>
      )}

      <Card>
        {loading ? (
          <Loading message="Cargando empresas..." />
        ) : companies.length === 0 ? (
          <EmptyState 
            message="No hay empresas registradas. Crea tu primera empresa para empezar."
            icon={<svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" /></svg>}
          />
        ) : (
          <Table
            columns={columns}
            data={companies}
            keyField="id"
          />
        )}
      </Card>

      <Modal isOpen={showModal} onClose={() => { setShowModal(false); setFormData({ name: '', tax_id: '' }); setEditingCompany(null); }} title={editingCompany ? 'Editar Empresa' : 'Nueva Empresa'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input label="Nombre" value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} required placeholder="Mi Empresa SA" />
          <Input label="RFC" value={formData.tax_id} onChange={e => setFormData({...formData, tax_id: e.target.value})} required placeholder="MES123456789" />
          <div className="flex justify-end gap-3 pt-4 border-t">
            <Button variant="secondary" type="button" onClick={() => { setShowModal(false); setFormData({ name: '', tax_id: '' }); setEditingCompany(null); }}>Cancelar</Button>
            <Button type="submit" disabled={submitting}>{submitting ? 'Guardando...' : (editingCompany ? 'Actualizar' : 'Crear')}</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}