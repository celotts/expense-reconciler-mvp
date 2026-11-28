import { useState, useEffect } from 'react';
import { companiesApi, type Company, type CompanyCreate } from '../services/api';
import { 
  Button, Input, Modal, Table, Card, Loading, EmptyState 
} from '../components/ui';
import { useEmpresa, usePermisos } from '../contextos/EmpresaContexto';

const ETIQUETA_ESTADO: Record<Company['estado'], string> = {
  activo: 'Activa',
  inactivo: 'Inactiva (se puede reactivar)',
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

  const handleDesactivar = async (id: string) => {
    if (!confirm('¿Desactivar esta empresa? Sus datos se conservan y podra reactivarla; mientras este inactiva no se podra trabajar en ella.')) return;
    try {
      await companiesApi.desactivar(id);
      trasCambiar();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al desactivar');
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
    { key: 'name', header: 'Nombre', render: (c: Company) => <span className="font-medium">{c.name}</span> },
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
            <Button variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); handleDesactivar(c.id); }}>Desactivar</Button>
          )}
          {c.estado === 'inactivo' && (
            <Button variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); handleActivar(c.id); }}>Activar</Button>
          )}
          {c.estado !== 'eliminado' && (
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