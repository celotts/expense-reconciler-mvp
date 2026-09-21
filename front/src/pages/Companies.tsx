import { useState, useEffect } from 'react';
import { companiesApi, type Company, type CompanyCreate, type CompanyUpdate } from '../services/api';
import { 
  Button, Input, Modal, Table, Card, Badge, Loading, EmptyState 
} from '../components/ui';

export function Companies() {
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
      const data = await companiesApi.list();
      setCompanies(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar empresas');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadCompanies(); }, []);

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

  const handleDelete = async (id: string) => {
    if (!confirm('¿Eliminar esta empresa? Se borrarán todos sus tickets y movimientos.')) return;
    try {
      await companiesApi.delete(id);
      loadCompanies();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al eliminar');
    }
  };

  const columns = [
    { key: 'name', header: 'Nombre', render: (c: Company) => <span className="font-medium">{c.name}</span> },
    { key: 'tax_id', header: 'RFC', render: (c: Company) => <code className="text-sm bg-gray-100 px-2 py-1 rounded">{c.tax_id}</code> },
    { key: 'created_at', header: 'Creada', render: (c: Company) => new Date(c.created_at).toLocaleDateString('es-MX') },
    { key: 'actions', header: 'Acciones', render: (c: Company) => (
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={() => openEditModal(c)}>Editar</Button>
        <Button variant="danger" size="sm" onClick={() => handleDelete(c.id)}>Eliminar</Button>
      </div>
    )},
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Empresas</h1>
          <p className="text-gray-500">Gestiona tus empresas y perfiles fiscales</p>
        </div>
        <Button onClick={openCreateModal}>Nueva Empresa</Button>
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
            columns={[
              { key: 'name', header: 'Nombre', render: (c: Company) => <span className="font-medium">{c.name}</span> },
              { key: 'tax_id', header: 'RFC', render: (c: Company) => <code className="text-sm bg-gray-100 px-2 py-1 rounded">{c.tax_id}</code> },
              { key: 'created_at', header: 'Creada', render: (c: Company) => new Date(c.created_at).toLocaleDateString('es-MX') },
              { key: 'actions', header: 'Acciones', render: (c: Company) => (
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); openEditModal(c); }}>Editar</Button>
                  <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); handleDelete(c.id); }}>Eliminar</Button>
                </div>
              )},
            ]}
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