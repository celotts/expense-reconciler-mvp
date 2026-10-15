/** La pantalla de entrar.
 *
 *  No hay formulario de "registrate" y no lo va a haber, y no es un
 *  descuido pendiente: el sistema guarda gastos, movimientos bancarios y
 *  veredictos que sostienen un cierre contable. Un alta publica es la forma mas
 *  rapida de que un tercero se siente a escribir ahi, y lo que escribe no se
 *  puede deshacer despues. Quien decide que alguien entra es quien tiene la
 *  base de datos, con `scripts/crear_usuario.py`.
 *
 *  Dos cosas que esta pantalla hace bien y que son easy de hacer mal:
 *
 *  **El error se muestra tal cual.** El servidor responde tres cosas distintas
 *  y las tres se muestran tal cual: credenciales malas, cuenta dada de baja y
 *  429 por intentos de mas. Reconvertirlas a un unico "no se pudo entrar"
 *  tira informacion que el usuario necesita: el "espera 42 segundos" del 429 es
 *  la diferencia entre que sepa que volver a intentarlo ya o que insista y se
 *  quede bloqueado mas tiempo.
 *
 *  **El formulario entero se deshabilita mientras espera la respuesta.** Con el
 *  scrypt del servidor (~100 ms por intento, a proposito) se pueden mandar dos
 *  pulsaciones seguidas y crear dos sesiones. Es un login, no un cobro, asi que
 *  el dano es pequeno, pero bloquear el formulario mientras espera es lo que
 *  evita tener que pensarlo.
 */
import { useState } from 'react';
import { entrar } from '../services/auth';
import { esSesionVencida, type AvisoSesion } from '../services/sesion';
import type { Usuario } from '../types/api';
import { Input, Button } from '../components/ui';

export function Login({
  onEntro,
  aviso,
}: {
  /** Recibe el usuario que devuelve el servidor, no un `void`.
   *
   *  Lo que se guarda en la sesion es el usuario que respondio el API, con su
   *  `nombre` y su `is_active` de este momento, y no el correo que se tecleo:
   *  la pantalla de entrar no sabe si la cuenta esta dada de baja ni como se
   *  llama, y mandarla a reconstruirlo con lo que tiene seria inventar datos. */
  onEntro: (usuario: Usuario) => void;
  /** Por que se pide entrar otra vez, si es que se sabe.
   *
   *  No es un detalle: si el token caduco a mitad de una captura, el archivo ya
   *  se consumio del selector y el trabajo no quedo guardado. Un login mudo deja
   *  al usuario pensando que solo hay que volver a entrar, y al reenviar sin
   *  volver a elegir el archivo descubre que no. El aviso lo dice, que es
   *  exactamente para lo que existe `avisoPorCaducidad`. */
  aviso?: AvisoSesion | null;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (enviando) return;

    setEnviando(true);
    setError(null);
    try {
      const usuario = await entrar(email.trim(), password);
      // No se guarda la pantalla anterior. Volver a donde estaba suena a
      // amabilidad y es una trampa: la transaccion anterior, si la hubo, ya se
      // perdio, y al volver la pantalla se recarga y muestra la lista como
      // estaba, y el usuario lee eso como "si se guardo". No se guardo.
      onEntro(usuario);
    } catch (err) {
      // Un `SesionVencida` aqui no puede pasar (el login va con `sinToken`) y
      // si pasara seria un fallo de programacion, no algo que la pantalla
      // tenga que adivinar. Menos mal que se distingue: si este `catch` no lo
      // separa, un 401 por cualquier otra razon se leeria como "credenciales
      // malas", que es justo la mentira que el servidor evita dar.
      if (err instanceof Error && !esSesionVencida(err)) {
        setError(err.message);
      } else {
        setError('No se pudo entrar. Intentalo de nuevo.');
      }
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-blue-600">Expense Reconciler</h1>
          <p className="text-sm text-gray-500 mt-1">Evidencia de cierre mensual</p>
        </div>

        {aviso && !error && (
          <div
            role="status"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2"
          >
            <p className="text-sm font-medium text-amber-900">{aviso.titulo}</p>
            <p className="text-sm text-amber-800 mt-0.5">{aviso.mensaje}</p>
          </div>
        )}

        <form
          onSubmit={enviar}
          className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 flex flex-col gap-4"
        >
          <Input
            label="Correo"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            disabled={enviando}
          />

          <Input
            label="Contraseña"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            disabled={enviando}
          />

          {error && (
            <div role="alert" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <Button type="submit" disabled={enviando}>
            {enviando ? 'Entrando...' : 'Entrar'}
          </Button>
        </form>

        <p className="text-xs text-gray-400 text-center mt-6">
          No hay autorregistro. Las cuentas las crea quien administra la base.
        </p>
      </div>
    </div>
  );
}
