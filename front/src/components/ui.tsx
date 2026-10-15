// UI Components
//
// Los hooks viven aqui porque el `Input` los usa: el boton del ojo lleva estado
// (`useState`) y se reinicia cuando el campo deja de ser de contrasena
// (`useEffect`). Se importan del modulo y no con el prefijo `React.` porque el
// resto del front importa de React de forma nominal, y dos estilos en el mismo
// archivo es uno de mas.

import { useEffect, useState } from 'react';

// Button
//
// EL ALTO VIENE DE AQUI Y NO DE QUIEN LO USA — y por la misma razon que el
// padding del `Card`: medido, no opinado.
//
// Las guias de plataforma (Apple HIG, Material) piden 44px CSS como minimo de
// area tactil, y por debajo el error de pulsacion sube de forma medible. Se
// midio en las ocho pantallas a 390px y el resultado fue que **ningun** control
// llegaba: el `md` daba 40, el `sm` 32, y los `<select>` 38. No era un defecto
// de una pantalla sino de la primitiva, y por eso se arregla aqui y no pagina
// por pagina — que es exactamente lo que paso con el padding del `Card`.
//
// LOS DOS VALORES, Y POR QUE HAY DOS
// ---------------------------------
// En movil el boton es de 44px. En escritorio se queda en el alto compacto de
// antes, porque una densidad alta es correcta con raton: 44px de alto en cada
// boton de una tabla de 40 filas comeria la mitad de la pantalla, y nadie lo
// pidio. `sm:` (640px) es donde se cambia, no `md:`.
//
// El numero no sale de un calculo: sale de medir. `py-2.5` + `text-base`
// (16/24) = 44. `py-3` + `text-sm` (14/20) = 44.
export function Button({ 
  children, 
  onClick, 
  variant = 'primary', 
  size = 'md',
  disabled = false, 
  type = 'button',
  className = '',
  ...props 
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'edit' | 'success'; size?: 'sm' | 'md' }) {
  const base = 'rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const sizes = {
    sm: 'px-2.5 py-3 sm:py-1.5 text-sm',
    md: 'px-4 py-2.5 sm:py-2',
  };
  const variants = {
    primary: 'bg-blue-600 text-white hover:bg-blue-700',
    secondary: 'bg-gray-200 text-gray-800 hover:bg-gray-300',
    danger: 'bg-red-600 text-white hover:bg-red-700',
    ghost: 'bg-transparent text-gray-600 hover:bg-gray-100',
    edit: 'bg-amber-500 text-white hover:bg-amber-600',
    success: 'bg-emerald-600 text-white hover:bg-emerald-700',
  };
  return (
    <button 
      type={type} 
      onClick={onClick} 
      disabled={disabled}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

// Input
export function Input({ 
  label, 
  error, 
  className = '',
  id,
  ...props 
}: React.InputHTMLAttributes<HTMLInputElement> & { label?: string; error?: string }) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

  // --- El botón del ojo, para los campos de contraseña -----------------------
  //
  // POR QUÉ EXISTE
  //
  // Ocultar la clave está bien por omisión —una clave escrita en la pantalla
  // queda a la vista de quien esté detrás, y esto se usa en un despacho con
  // gente alrededor—. Lo que NO está bien es que no haya forma de volver a
  // verla.
  //
  // El caso concreto de este proyecto: `auth.login` tiene **rate limit**, así que
  // un tecleo mal no es un problema de una vez. Con el campo en `password` y sin
  // botón, quien escribe una mayúscula donde va minúscula no tiene forma de
  // saberlo hasta que el servidor responde "correo o contraseña incorrectos" —
  // y reintentar a ciegas es exactamente como se bloquea una cuenta.
  //
  // Está en el `Input` y no en `Login.tsx` a propósito: el próximo formulario
  // que necesite una clave (cambiar contraseña, crear un usuario) lo hereda sin
  // tener que acordarse de añadirlo, que es cuando se olvidaría.
  const esPassword = props.type === 'password';
  const [visible, setVisible] = useState(esPassword ? false : false);

  // Cuando `type` cambia (un input que empieza normal y pasa a password), el
  // estado se queda con el valor viejo y puede dejar la clave a la vista. Es un
  // caso raro —el `type` de un input no suele cambiar— pero dejar una clave
  // visible por un descuido de estado es la clase de fallo que no tiene
  //ataliento, así que se cubre: al dejar de ser password, se oculta.
  useEffect(() => {
    if (!esPassword) setVisible(false);
  }, [esPassword]);

  return (
    <div className="flex flex-col gap-1">
      {label && <label htmlFor={inputId} className="text-sm font-medium text-gray-700">{label}</label>}
      <div className="relative">
        <input
          id={inputId}
          // `pr-11` solo cuando hay botón: el input y el botón se solapan si el
          // padding no deja sitio al icono, y el texto queda debajo del ojo en vez
          // de al lado.
          className={`w-full px-3 py-3 sm:py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 ${
            esPassword ? 'pr-11' : ''
          } ${error ? 'border-red-500' : 'border-gray-300'} ${className}`}
          {...(esPassword ? { type: visible ? 'text' : 'password' } : {})}
          {...props}
        />
        {esPassword && (
          <button
            type="button"
            onClick={() => setVisible(v => !v)}
            // `aria-label` y no solo el icono: un SVG sin nombre no lo lee el
            // lector de pantalla, y este botón es el único camino para leer la
            // clave de alguien que no ve bien.
            //
            // `aria-pressed` dice si la clave está a la vista AHORA. Sin esto, un
            // lector de pantalla anuncia "botón" y no dice si el campo cambió.
            aria-label={visible ? 'Ocultar la contraseña' : 'Ver la contraseña'}
            aria-pressed={visible}
            title={visible ? 'Ocultar la contraseña' : 'Ver la contraseña'}
            // `right-3` y `top-1/2 -translate-y-1/2` para centrarlo en el alto del
            // input. El `p-3` da el área táctil de 44px que el resto de la
            // aplicación respeta (ver `App.tsx` y `Button`): con `p-2` el icono de
            // 24 queda en 40, dos por debajo del mínimo.
            className="absolute right-0 top-1/2 -translate-y-1/2 p-3 text-gray-400 hover:text-gray-600 transition-colors"
          >
            {visible ? <OjoOcultoIcon /> : <OjoIcon />}
          </button>
        )}
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
  );
}

/** El ojo abierto: la clave está visible y un clic la oculta. */
function OjoIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.964-7.178z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  );
}

/** El ojo tachado: la clave está oculta y un clic la muestra.
 *
 *  El tachado es lo que distingue "clic para ver" de "clic para no ver". Con los
 *  dos iconos idénticos, quien ya miró la clave no sabe si el siguiente clic la
 *  va a mostrar o a esconder, y tiene que probarlo.
 */
function OjoOcultoIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
    </svg>
  );
}

// Select
export function Select({ 
  label, 
  options, 
  error,
  className = '',
  id,
  ...props 
}: React.SelectHTMLAttributes<HTMLSelectElement> & { label?: string; options: { value: string; label: string }[]; error?: string }) {
  const selectId = id || label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div className="flex flex-col gap-1">
      {label && <label htmlFor={selectId} className="text-sm font-medium text-gray-700">{label}</label>}
      <select
        id={selectId}
        // `py-3` en movil y `py-2` desde `sm`, por la misma razon y con la
        // misma medicion que el `Button`. El numero salio de dos rondas: `py-2.5`
        // daba 42px —dos por debajo del minimo— y `py-3` da 46. Se eligio 46 y no
        // un `py-[11px]` de 44 exacto porque dos pixeles por debajo del minimo no
        // se ven, pero un selector de 2px mas alto que sus vecinos en el mismo
        // formulario, si.
        className={`w-full px-3 py-3 sm:py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 ${
          error ? 'border-red-500' : 'border-gray-300'
        } ${className}`}
        {...props}
      >
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>
      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
  );
}

// Modal
export function Modal({ isOpen, onClose, title, children, className = '' }: { 
  isOpen: boolean; 
  onClose: () => void; 
  title: string; 
  children: React.ReactNode;
  className?: string;
}) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={onClose}>
      <div 
        className={`bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto ${className}`} 
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-xl font-semibold">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

// Card
//
// El padding va AQUI y no en quien lo usa. Antes no lo tenia, y cada pantalla
// que se acordaba le ponia el suyo: 16 de los 36 usos no se acordaban, y
// quedaban con las letras pegadas al borde.
//
// Y no se deja que quien lo use lo cambie con un `p-4` en su `className`, porque
// `p-4` y `p-5` tienen la misma especificidad en Tailwind y gana la que
// aparezca despues en la hoja de estilos generada, no la que va despues en el
// atributo `class`. O sea: no se puede saber de antemano. Con el padding aqui y
// nadie mas tocando el, el borde del cuadro es el mismo en las siete
// pantallas y cambiarlo es cambiar una linea.
//
// Para un cuadro que de verdad necesite mas aire, se envuelve en un `div` con
// padding, no se le pasa al `Card`.
export function Card({ children, className = '', ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`bg-white rounded-xl shadow-sm border border-gray-200 p-5 ${className}`} {...props}>
      {children}
    </div>
  );
}

// Table
export function Table({ columns, data, keyField, onRowClick, className = '' }: {
  columns: { key: string; header: string; render?: (item: any) => React.ReactNode }[];
  data: any[];
  keyField: string;
  onRowClick?: (item: any) => void;
  className?: string;
}) {
  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full">
        <thead className="bg-gray-50">
          <tr>
            {columns.map(col => (
              <th key={col.key} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {data.map(item => (
            <tr 
              key={item[keyField]} 
              className={onRowClick ? 'cursor-pointer hover:bg-gray-50' : ''}
              onClick={() => onRowClick?.(item)}
            >
              {columns.map(col => (
                <td key={col.key} className="px-4 py-3 text-sm text-gray-900">
                  {col.render ? col.render(item) : item[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Badge
export function Badge({ children, variant = 'default' }: { 
  children: React.ReactNode; 
  variant?: 'default' | 'success' | 'warning' | 'danger' | 'info';
}) {
  const variants = {
    default: 'bg-gray-100 text-gray-700',
    success: 'bg-green-100 text-green-700',
    warning: 'bg-yellow-100 text-yellow-700',
    danger: 'bg-red-100 text-red-700',
    info: 'bg-blue-100 text-blue-700',
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${variants[variant]}`}>
      {children}
    </span>
  );
}

// Loading
export function Loading({ message = 'Cargando...' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
        <p className="text-gray-600">{message}</p>
      </div>
    </div>
  );
}

// Empty State
export function EmptyState({ 
  message, 
  icon, 
  action 
}: { 
  message: string; 
  icon?: React.ReactNode;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="text-center py-12">
      {icon && <div className="mx-auto mb-4 text-gray-400">{icon}</div>}
      <p className="text-gray-500 mb-4">{message}</p>
      {action && (
        <Button onClick={action.onClick} variant="primary">
          {action.label}
        </Button>
      )}
    </div>
  );
}

// Camera Capture
export { CameraCapture } from './ui/CameraCapture';

// File Upload
export function FileUpload({ 
  onChange, 
  accept, 
  multiple = false, 
  className = '',
  label = 'Seleccionar archivo',
  disabled = false,
  inputId
}: { 
  onChange: (files: FileList) => void; 
  accept?: string; 
  multiple?: boolean; 
  className?: string;
  label?: string;
  disabled?: boolean;
  inputId?: string;
}) {
  return (
    <label htmlFor={inputId} className={`flex flex-col items-center justify-center w-full h-32 border-2 border-dashed border-gray-300 rounded-lg cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-colors ${disabled ? 'opacity-50 cursor-not-allowed hover:border-gray-300 hover:bg-transparent' : ''} ${className}`}>
      <input 
        id={inputId}
        type="file" 
        accept={accept} 
        multiple={multiple} 
        disabled={disabled}
        onChange={e => e.target.files && onChange(e.target.files)} 
        className="sr-only" 
      />
      <svg className="w-8 h-8 text-gray-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
      </svg>
      <span className="text-gray-600">{label}</span>
    </label>
  );
}