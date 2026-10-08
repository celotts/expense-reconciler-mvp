// UI Components

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
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost'; size?: 'sm' | 'md' }) {
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
  return (
    <div className="flex flex-col gap-1">
      {label && <label htmlFor={inputId} className="text-sm font-medium text-gray-700">{label}</label>}
      <input
        id={inputId}
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
      />
      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
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