/** Administrar cuentas y roles por empresa.
 *
 *  Esta pantalla NO decide nada: el menu solo la ofrece a `owner`/`admin` y el
 *  servidor vuelve a comprobar el rol en cada peticion. La contrasena de firma
 *  se pide aqui y se envia en el header `X-Contrasena-Actual`; no se guarda.
 *
 *  Los datos iniciales se pueden inyectar por props para que el verificador
 *  mire HTML renderizado y no el retorno de una funcion: `tsc` pasa con un
 *  `undefined` en pantalla, y eso es exactamente lo que aqui se quiere ver.
 */
import { useCallback, useEffect, useState } from 'react';
import { AvisoSinEmpresa } from '../contextos/Empresa';
import { useEmpresa } from '../contextos/EmpresaContexto';
import { Button, Card, Input, Modal, Select, Table } from '../components/ui';
import { usuariosApi } from '../services/usuarios';
import type {
  MembresiaCreate,
  MembresiaUpdate,
  MembresiaUsuario,
  Usuario,
  UsuarioCreate,
  UsuarioUpdate,
} from '../types/api';

type Pestana = 'usuarios' | 'roles';

const ROLES = [
  { value: 'owner', label: 'Owner' },
  { value: 'admin', label: 'Admin' },
  { value: 'accountant', label: 'Contador' },
  { value: 'operator', label: 'Operador' },
  { value: 'viewer', label: 'Solo lectura' },
];

const PERFILES = [
  { value: '', label: 'Sin perfil' },
  { value: 'digitalizador', label: 'Digitalizador' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'admin', label: 'Administrador' },
  { value: 'consultor', label: 'Consultor' },
];

interface Props {
  usuariosIniciales?: Usuario[];
  membresiasIniciales?: MembresiaUsuario[];
  /** Solo para que el verificador entre en una pestaña sin simular un clic:
   *  `renderToStaticMarkup` no corre `useEffect` ni eventos. En la app real
   *  nadie los pasa y la pantalla empieza en Usuarios, como siempre. */
  pestanaInicial?: Pestana;
  modalInicial?: 'crear' | 'editar' | 'reset' | 'asignar' | null;
  seleccionadoInicial?: Usuario | null;
}

export function Mantenimiento({
  usuariosIniciales,
  membresiasIniciales,
  pestanaInicial = 'usuarios',
  modalInicial = null,
  seleccionadoInicial = null,
}: Props) {
  const { empresaActiva } = useEmpresa();
  const companyId = empresaActiva?.company_id;
  const [pestana, setPestana] = useState<Pestana>(pestanaInicial);
  const [firma, setFirma] = useState('');
  const [usuarios, setUsuarios] = useState<Usuario[]>(usuariosIniciales ?? []);
  const [membresias, setMembresias] = useState<MembresiaUsuario[]>(membresiasIniciales ?? []);
  const [error, setError] = useState('');
  const [aviso, setAviso] = useState('');
  const [modal, setModal] = useState<'crear' | 'editar' | 'reset' | 'asignar' | null>(modalInicial);
  const [seleccionado, setSeleccionado] = useState<Usuario | null>(seleccionadoInicial);

  const [nuevo, setNuevo] = useState<UsuarioCreate>({
    email: '', nombre: '', password: '', perfil: '', grupo: '',
  });
  const [edicion, setEdicion] = useState<UsuarioUpdate>({});
  const [claveNueva, setClaveNueva] = useState('');
  const [nuevaMembresia, setNuevaMembresia] = useState<MembresiaCreate>({
    user_id: '', rol: 'operator', activo: true,
  });

  const recargar = useCallback(async (): Promise<void> => {
    const [cuentas, roles] = await Promise.all([
      usuariosApi.listar(),
      usuariosApi.listarMembresias(),
    ]);
    setUsuarios(cuentas);
    setMembresias(roles);
  }, []);

  useEffect(() => {
    if (usuariosIniciales && membresiasIniciales) return;
    if (!companyId) return;
    void recargar().catch((e: unknown) => {
      setError(e instanceof Error ? e.message : 'No se pudieron cargar las cuentas');
    });
  }, [companyId, usuariosIniciales, membresiasIniciales, recargar]);

  function ejecutar(fn: () => Promise<void>, mensaje: string): void {
    setError('');
    setAviso('');
    if (!firma) {
      setError('Escribe tu contrasena para firmar la operacion.');
      return;
    }
    fn()
      .then(async () => {
        setModal(null);
        setAviso(mensaje);
        if (!usuariosIniciales || !membresiasIniciales) await recargar();
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'No se pudo guardar'));
  }

  function abrirEdicion(usuario: Usuario): void {
    setSeleccionado(usuario);
    setEdicion({
      nombre: usuario.nombre,
      perfil: usuario.perfil ?? '',
      grupo: usuario.grupo ?? '',
      is_active: usuario.is_active,
    });
    setModal('editar');
  }

  function abrirReset(usuario: Usuario): void {
    setSeleccionado(usuario);
    setClaveNueva('');
    setModal('reset');
  }

  const cuentasSinRol = usuarios.filter(
    (u) => !membresias.some((m) => m.user_id === u.id),
  );
  useEffect(() => {
    const candidato = cuentasSinRol[0];
    if (candidato && !nuevaMembresia.user_id) {
      setNuevaMembresia((actual) => ({ ...actual, user_id: candidato.id }));
    }
  }, [cuentasSinRol, nuevaMembresia.user_id]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Mantenimiento</h1>
        <p className="text-sm text-gray-600 mt-1">
          Cuentas y roles de la empresa activa. Solo owner o admin pueden entrar.
        </p>
      </div>

      <AvisoSinEmpresa mensaje="Elige una empresa antes de administrar cuentas o roles." />

      {error && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {aviso && (
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          {aviso}
        </div>
      )}

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Contrasena del administrador"
            type="password"
            value={firma}
            onChange={(e) => setFirma(e.target.value)}
            placeholder="Se pide en cada sesion"
          />
          <div className="flex items-end">
            <p className="text-xs text-gray-500 pb-2">
              Firma las operaciones que afectan a otra cuenta. No se guarda ni se muestra.
            </p>
          </div>
        </div>
      </Card>

      <div className="flex gap-2 border-b border-gray-200">
        <button
          type="button"
          data-pestana="usuarios"
          onClick={() => setPestana('usuarios')}
          className={`px-4 py-2 text-sm font-medium ${pestana === 'usuarios' ? 'border-b-2 border-blue-600 text-blue-700' : 'text-gray-600'}`}
        >
          Usuarios
        </button>
        <button
          type="button"
          data-pestana="roles"
          onClick={() => setPestana('roles')}
          className={`px-4 py-2 text-sm font-medium ${pestana === 'roles' ? 'border-b-2 border-blue-600 text-blue-700' : 'text-gray-600'}`}
        >
          Roles por empresa
        </button>
      </div>

      {pestana === 'usuarios' ? (
        <Card>
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold">Cuentas del sistema</h2>
            <Button onClick={() => setModal('crear')}>Nueva cuenta</Button>
          </div>
          <Table
            columns={[
              { key: 'email', header: 'Correo' },
              { key: 'nombre', header: 'Nombre' },
              { key: 'perfil', header: 'Perfil', render: (u: Usuario) => u.perfil ?? '—' },
              {
                key: 'is_active', header: 'Estado',
                render: (u: Usuario) => u.is_active ? 'Activa' : 'Dada de baja',
              },
              {
                key: 'acciones', header: 'Acciones',
                render: (u: Usuario) => (
                  <div className="flex gap-2">
                    <Button size="sm" variant="edit" onClick={() => abrirEdicion(u)}>Editar</Button>
                    <Button size="sm" variant="secondary" onClick={() => abrirReset(u)}>
                      Resetear clave
                    </Button>
                  </div>
                ),
              },
            ]}
            data={usuarios}
            keyField="id"
          />
        </Card>
      ) : (
        <Card>
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold">Acceso en {empresaActiva?.name ?? 'esta empresa'}</h2>
            <Button onClick={() => setModal('asignar')} disabled={cuentasSinRol.length === 0}>
              Asignar rol
            </Button>
          </div>
          <Table
            columns={[
              { key: 'email', header: 'Correo' },
              { key: 'nombre', header: 'Nombre' },
              { key: 'rol', header: 'Rol' },
              {
                key: 'activo', header: 'Acceso',
                render: (m: MembresiaUsuario) => m.activo ? 'Activo' : 'Sin acceso',
              },
              {
                key: 'acciones', header: 'Acciones',
                render: (m: MembresiaUsuario) => (
                  <div className="flex gap-2">
                    <Select
                      aria-label={`Rol de ${m.email}`}
                      value={m.rol}
                      options={ROLES}
                      onChange={(e) => ejecutar(
                        () => usuariosApi.actualizarMembresia(m.user_id, { rol: e.target.value as MembresiaUpdate['rol'] }, firma).then(() => undefined),
                        'Rol actualizado.',
                      )}
                    />
                    <Button
                      size="sm"
                      variant={m.activo ? 'danger' : 'success'}
                      onClick={() => ejecutar(
                        () => usuariosApi.actualizarMembresia(m.user_id, { activo: !m.activo }, firma).then(() => undefined),
                        m.activo ? 'Acceso retirado en esta empresa.' : 'Acceso restaurado en esta empresa.',
                      )}
                    >
                      {m.activo ? 'Dar de baja' : 'Reactivar'}
                    </Button>
                  </div>
                ),
              },
            ]}
            data={membresias}
            keyField="user_id"
          />
        </Card>
      )}

      <Modal isOpen={modal === 'crear'} onClose={() => setModal(null)} title="Nueva cuenta">
        <div className="space-y-4">
          <Input label="Correo" type="email" value={nuevo.email}
            onChange={(e) => setNuevo({ ...nuevo, email: e.target.value })} />
          <Input label="Nombre" value={nuevo.nombre}
            onChange={(e) => setNuevo({ ...nuevo, nombre: e.target.value })} />
          <Input label="Contrasena inicial" type="password" value={nuevo.password}
            onChange={(e) => setNuevo({ ...nuevo, password: e.target.value })} />
          <Select label="Perfil (etiqueta)" value={nuevo.perfil ?? ''} options={PERFILES}
            onChange={(e) => setNuevo({ ...nuevo, perfil: e.target.value || undefined })} />
          <Input label="Grupo" value={nuevo.grupo ?? ''}
            onChange={(e) => setNuevo({ ...nuevo, grupo: e.target.value || undefined })} />
          <Button onClick={() => ejecutar(
            () => usuariosApi.crear(nuevo, firma).then(() => undefined),
            'Cuenta creada. Asignale un rol en la pestaña Roles por empresa.',
          )}>Crear cuenta</Button>
          <p className="text-xs text-gray-500">
            Crear la cuenta no da acceso a ninguna empresa: eso se hace en Roles por empresa.
          </p>
        </div>
      </Modal>

      <Modal isOpen={modal === 'editar'} onClose={() => setModal(null)} title={`Editar a ${seleccionado?.nombre ?? ''}`}>
        <div className="space-y-4">
          <Input label="Nombre" value={edicion.nombre ?? ''}
            onChange={(e) => setEdicion({ ...edicion, nombre: e.target.value })} />
          <Select label="Perfil (etiqueta)" value={edicion.perfil ?? ''} options={PERFILES}
            onChange={(e) => setEdicion({ ...edicion, perfil: e.target.value })} />
          <Input label="Grupo" value={edicion.grupo ?? ''}
            onChange={(e) => setEdicion({ ...edicion, grupo: e.target.value })} />
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={edicion.is_active ?? true}
              onChange={(e) => setEdicion({ ...edicion, is_active: e.target.checked })} />
            Cuenta activa
          </label>
          <Button onClick={() => ejecutar(
            () => usuariosApi.actualizar(seleccionado!.id, edicion, firma).then(() => undefined),
            'Cuenta actualizada.',
          )}>Guardar</Button>
          <p className="text-xs text-gray-500">
            No se puede cambiar el correo: esta dentro de la clave del bloqueo de login.
          </p>
        </div>
      </Modal>

      <Modal isOpen={modal === 'reset'} onClose={() => setModal(null)} title={`Resetear clave de ${seleccionado?.email ?? ''}`}>
        <div className="space-y-4">
          <Input label="Contrasena nueva" type="password" value={claveNueva}
            onChange={(e) => setClaveNueva(e.target.value)} />
          <Button variant="danger" onClick={() => ejecutar(
            () => usuariosApi.resetearContrasena(seleccionado!.id, claveNueva, firma),
            'Clave actualizada. Los tokens ya emitidos siguen vivos hasta caducar.',
          )}>Fijar contrasena</Button>
          <p className="text-xs text-gray-500">
            No se necesita la clave de la otra cuenta. Se necesita la tuya, arriba.
          </p>
        </div>
      </Modal>

      <Modal isOpen={modal === 'asignar'} onClose={() => setModal(null)} title="Asignar rol en la empresa">
        <div className="space-y-4">
          <Select label="Cuenta" value={nuevaMembresia.user_id}
            options={cuentasSinRol.map((u) => ({ value: u.id, label: `${u.nombre} (${u.email})` }))}
            onChange={(e) => setNuevaMembresia({ ...nuevaMembresia, user_id: e.target.value })} />
          <Select label="Rol" value={nuevaMembresia.rol} options={ROLES}
            onChange={(e) => setNuevaMembresia({
              ...nuevaMembresia, rol: e.target.value as MembresiaCreate['rol'],
            })} />
          <Button onClick={() => ejecutar(
            () => usuariosApi.asignarMembresia(nuevaMembresia, firma).then(() => undefined),
            'Rol asignado.',
          )}>Asignar</Button>
          <p className="text-xs text-gray-500">
            El ultimo owner o admin de la empresa no puede quitarse el acceso.
          </p>
        </div>
      </Modal>
    </div>
  );
}
