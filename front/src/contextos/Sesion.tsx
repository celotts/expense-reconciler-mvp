/** La puerta: decide si se ve la aplicacion o se ve el login.
 *
 *  Va en un contexto y no en `App.tsx` porque hacen falta DOS cosas a la vez:
 *  el usuario (para pintarlo en la barra) y el aviso de caducidad (para poder
 *  decir "no se guardo nada" en vez de un "vuelve a entrar" pelado). Con dos
 *  `useState` sueltos en `App`, cada uno acaba pasandose por props a las siete
 *  pantallas, y basta con que una se forget del prop para que la app se quede
 *  en un estado en el que el login esta puesto y todavia se ve la tabla.
 *
 *  **Mientras comprueba, no enseña NADA.** Ni la aplicacion ni el login: un
 *  spinner. Lo contrario es el fallo clasico de esto, y es muy feo: al abrir la
 *  pestana se ve un instante la aplicacion entera con datos vacios, y luego
 *  salta el login. El usuario ve una pantalla que no existe, y si pulsa algo en
 *  ese cuarto de segundo la peticion sale sin token.
 */
import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from 'react';
import type { Usuario } from '../types/api';
import { comprobarSesion, salir } from '../services/auth';
import { alCaducar, estaVencido, leerToken, type AvisoSesion } from '../services/sesion';

interface SesionContextValue {
  /** `null` = todavia no se sabe. La app NO se monta en ese estado: no hay
   *  forma de distinguir "cargando" de "no hay sesion" si el valor es el mismo. */
  usuario: Usuario | null;
  cargando: boolean;
  /** El aviso de caducidad, para mostrarlo encima del login. Se limpia al
   *  entrar, para que el exito borre el mensaje de fracaso anterior. */
  aviso: AvisoSesion | null;
  entro: (u: Usuario) => void;
  cerrarSesion: () => void;
}

const SesionContext = createContext<SesionContextValue | null>(null);

export function SesionProvider({ children }: { children: ReactNode }) {
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<AvisoSesion | null>(null);

  // La comprobacion de arranque. Se hace UNA vez y no en cada render: si
  // colgara de un `useEffect` sin dependencias, cada cambio de estado
  // repreguntaria al servidor y un fallo de red dejaria la pantalla en bucle.
  useEffect(() => {
    let vivo = true;

    (async () => {
      try {
        const u = await comprobarSesion();
        // `vivo` porque el componente puede desmontarse mientras espera (React
        // 18 en modo estricto monta, desmonta y vuelve a montar). Escribir en
        // el estado de un componente ya desmontado es el aviso de React y, en
        // una app con dos personas usando el mismo reloj, el sintoma aparece
        // como un fallo intermitente que nadie reproduce.
        if (vivo) setUsuario(u);
      } catch {
        // Un fallo de red al COMPROBAR no es un fallo de sesion: no se come el
        // token ni cierra la puerta. Se deja en `null` y se muestra el login,
        // pero el token sigue ahi y al siguiente intento puede funcionar.
        if (vivo) setUsuario(null);
      } finally {
        if (vivo) setCargando(false);
      }
    })();

    return () => {
      vivo = false;
    };
  }, []);

  // El aviso de caducidad. `api.ts` lo dispara en cada 401, desde donde sea que
  // venga la peticion, y este es el unico sitio que lo escucha.
  useEffect(
    () =>
      alCaducar(a => {
        setUsuario(null);
        setAviso(a);
        // El token se borra aqui y no en quien dispara el aviso, para que las
        // tres cosas (usuario fuera, token fuera, aviso puesto) pasen siempre
        // juntas. Si el token se quedara, el `comprobarSesion` del siguiente
        // arranque lo daria por bueno y la app abriria con la sesion que acabo
        // de expirar.
        borrarSiCaduca();
      }),
    [],
  );

  const entro = useCallback((u: Usuario) => {
    setUsuario(u);
    setAviso(null);
  }, []);

  const cerrarSesion = useCallback(() => {
    salir();
    setUsuario(null);
    setAviso(null);
  }, []);

  return (
    <SesionContext.Provider value={{ usuario, cargando, aviso, entro, cerrarSesion }}>
      {children}
    </SesionContext.Provider>
  );
}

/** Se va solo si el token esta realmente muerto.
 *
 *  El aviso tambien se dispara por otras razones (un 401 con el API reiniciado
 *  por ejemplo), y en ese caso el token puede seguir siendo valido: borrarlo a
 *  ciegas cerraria una sesion que no habia caducado y el usuario tendria que
 *  volver a entrar sin motivo. */
function borrarSiCaduca(): void {
  const token = leerToken();
  if (!token || estaVencido(token)) {
    salir();
  }
}

export function useSesion(): SesionContextValue {
  const ctx = useContext(SesionContext);
  if (!ctx) {
    throw new Error('useSesion debe usarse dentro de SesionProvider');
  }
  return ctx;
}
