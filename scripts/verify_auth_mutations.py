#!/usr/bin/env python3
"""Verifica que los tests de la sesion detecten las regresiones.

Un test que pasa no demuestra nada sobre el codigo: puede que este probando lo
que cree, o puede que no este probando nada. La unica forma de saber cual de
las dos es romper el codigo a proposito y ver si el test se da cuenta.

Las mutaciones de aqui deshacen decisiones que estan documentadas en el codigo,
con cambios que se han hecho de verdad, no inventados para que el script quede
bonito. Y hay tres que son especialmente importantes, porque son la diferencia
entre "el token se verifica" y "se verifica el token":

- Comparar la firma con `==` en vez de `hmac.compare_digest`. Sobrevive si el
  test solo pasa un token con la firma totalmente distinta, que es el caso
  facil. Lo que filtra es cuando alguien intenta falsificar un token valido
  cambiandole un byte: con `==` la diferencia sale en el primer caracter y se
  nota, y con constante no.
- No comprobar la vigencia. Un token sin `exp` checking es un token eterno.
- Consultar al usuario solo por los claims, sin volver a la base. Sobrevive con
  todos los tests de autenticacion en verde y deja la puerta de que dar de baja
  a alguien no sirva de nada hasta que caduque su token.

Uso:  python3 scripts/verify_auth_mutations.py
Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import atexit
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

SEGURIDAD = "app/core/security.py"
DEPS = "app/core/deps.py"
LIMITE = "app/core/rate_limit.py"
AUTH = "app/api/auth.py"
CABLEADO = "app/api/api_router.py"
CONFIG = "app/core/config.py"

UNIT = "tests/unit/test_security.py"
API = "tests/integration/test_auth_api.py"
AUDIT = "tests/integration/test_audit_trail.py"
CONFIG_T = "tests/unit/test_config_secrets.py"

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    # -----------------------------------------------------------------------
    # La puerta: que este protegida
    # -----------------------------------------------------------------------
    (
        "el router de la v1 deja de pedir token",
        CABLEADO,
        "    dependencies=[Depends(get_current_user)],\n)\napi_router.include_router(\n    tickets_router,",
        ")\napi_router.include_router(\n    tickets_router,",
        [API],
    ),
    (
        "los tickets se vuelven a dejar abiertos",
        CABLEADO,
        '    prefix="/tickets",\n    tags=["Tickets"],\n    dependencies=[Depends(get_current_user)],',
        '    prefix="/tickets",\n    tags=["Tickets"],',
        [API],
    ),
    (
        "la cuenta dada de baja conserva el token",
        DEPS,
        "    if not usuario.is_active:\n        raise _no_autorizado(\"La cuenta esta dada de baja\")",
        "    if False:\n        raise _no_autorizado(\"La cuenta esta dada de baja\")",
        [API],
    ),
    (
        "el token decide quien es sin consultar la base",
        DEPS,
        "    usuario = (\n        await db.execute(select(UserModel).where(UserModel.id == usuario_id))\n    ).scalar_one_or_none()\n\n    if usuario is None:\n        raise _no_autorizado(\"El token no es valido\")",
        "    class usuario:  # el token basta\n        is_active = True\n        email = claims.get('email', 'alguien@desconocido.mx')\n        nombre = 'Alguien'\n        id = usuario_id",
        [API],
    ),
    (
        "el sub sin convertir revienta la consulta en vez de dar 401",
        DEPS,
        "    try:\n        usuario_id = UUID(claims[\"sub\"])\n    except (ValueError, AttributeError, TypeError) as exc:\n        raise _no_autorizado(\"El token no es valido\") from exc",
        "    usuario_id = claims[\"sub\"]",
        [API],
    ),
    # -----------------------------------------------------------------------
    # El token: la parte que mas se rompe cuando se reimplementa a mano
    # -----------------------------------------------------------------------
    (
        "la firma se compara con igualdad en vez de en constante",
        SEGURIDAD,
        "    if not hmac.compare_digest(firma, esperada):",
        "    if firma != esperada:",
        [UNIT],
    ),
    (
        "la vigencia deja de comprobarse",
        SEGURIDAD,
        '    if expira <= int(time.time()):\n        raise TokenCaducado("el token ya caduco")',
        "    if False:\n        raise TokenCaducado(\"el token ya caduco\")",
        [UNIT, API],
    ),
    (
        "el token caducado se confunde con uno mal formado",
        SEGURIDAD,
        "    if expira <= int(time.time()):\n        raise TokenCaducado(\"el token ya caduco\")",
        '    if expira <= int(time.time()):\n        raise ErrorDeToken("el token ya caduco")',
        [UNIT],
    ),
    (
        "el algoritmo lo decide el atacante",
        SEGURIDAD,
        '    if not isinstance(cabecera, dict) or cabecera.get("alg") != "HS256":\n        raise ErrorDeToken("algoritmo no soportado")',
        '    if not isinstance(cabecera, dict):\n        raise ErrorDeToken("algoritmo no soportado")',
        [UNIT, API],
    ),
    (
        "la cabecera deja de comprobarse del todo",
        SEGURIDAD,
        "    if not isinstance(cabecera, dict) or cabecera.get(\"alg\") != \"HS256\":\n        raise ErrorDeToken(\"algoritmo no soportado\")",
        "    pass",
        [UNIT, API],
    ),
    (
        "el tipo de token deja de comprobarse",
        SEGURIDAD,
        '    if cuerpo.get("typ") != "access":\n        raise ErrorDeToken("no es un token de acceso")',
        '    if False:\n        raise ErrorDeToken("no es un token de acceso")',
        [UNIT],
    ),
    (
        "la clave se captura al importar y no se vuelve a leer",
        SEGURIDAD,
        "    esperada = hmac.new(\n            settings.SECRET_KEY.encode(\"utf-8\"),",
        "    esperada = hmac.new(\n            _CLAVE_CAPTURADA.encode(\"utf-8\"),",
        [UNIT],
    ),
    (
        "el sub deja de ser obligatorio",
        SEGURIDAD,
        '    if not cuerpo.get("sub"):\n        raise ErrorDeToken("el token no lleva sub")',
        "    pass",
        [UNIT],
    ),
    (
        "el exp deja de ser obligatorio",
        SEGURIDAD,
        '    expira = cuerpo.get("exp")\n    if not isinstance(expira, int):\n        raise ErrorDeToken("el token no lleva exp")',
        "    expira = cuerpo.get(\"exp\", 2**40)",
        [UNIT],
    ),
    # -----------------------------------------------------------------------
    # Las contrasenas
    # -----------------------------------------------------------------------
    (
        "la comparacion de la contrasena filtra por tiempo",
        SEGURIDAD,
        "    return hmac.compare_digest(derivada, esperado)",
        "    return derivada == esperado",
        [UNIT],
    ),
    (
        "la sal desaparece y todos los hashes se parecen",
        SEGURIDAD,
        "    sal = secrets.token_bytes(_SAL_LEN)",
        "    sal = b\"sal-fija-16byt\"",
        [UNIT],
    ),
    (
        "el hash de trampa deja de ser un hash de trampa",
        SEGURIDAD,
        "        _TRAMPA_CACHEADA = hashear_contrasena(secrets.token_urlsafe(32))",
        '        _TRAMPA_CACHEADA = hashear_contrasena("esta-es-la-contrasena-de-alguien")',
        [UNIT],
    ),
    (
        "un hash corrupto revienta en vez de responder que no verifica",
        SEGURIDAD,
        "    except (ValueError, TypeError):\n        # Hash mal formado o de otra version: no es una contrasena valida, y\n        # no es una excepcion que le sirva de nada a quien llama.\n        return False",
        "    except TypeError:\n        return False",
        [UNIT],
    ),
    (
        "la version del formato deja de comprobarse",
        SEGURIDAD,
        "        version, n, r, p, sal_b64, esperado_b64 = guardado.split(\"$\")\n        if version != _VERSION_HASH:\n            return False",
        "        version, n, r, p, sal_b64, esperado_b64 = guardado.split(\"$\")",
        [UNIT],
    ),
    # -----------------------------------------------------------------------
    # El login: el mensaje unico y el tiempo de respuesta
    # -----------------------------------------------------------------------
    (
        "el correo inexistente dice que no existe",
        AUTH,
        "    if usuario is None:\n        # Se gasta el mismo tiempo que con una contrasena real.",
        '    if usuario is None:\n        raise HTTPException(\n            status_code=401,\n            detail="Ese correo no esta dado de alta",\n        )\n        # Se gasta el mismo tiempo que con una contrasena real.',
        [API],
    ),
    (
        "deja de gastarse el tiempo con un correo inexistente",
        AUTH,
        "        verificar_contrasena(datos.password, hash_de_trampa())\n        bloqueados = anotar_intento_fallido(correo, ip)",
        "        bloqueados = anotar_intento_fallido(correo, ip)",
        [API],
    ),
    (
        "la cuenta dada de baja se anuncia en el login",
        AUTH,
        "    if not usuario.is_active:\n        # Tampoco se dice \"tu cuenta esta dada de baja\" con este mismo codigo.\n        # Lo diria el 403 mas adelante, cuando alguien intente usar un token\n        # suyo. Aqui el login falla igual que con una contrasena mala.\n        raise _credenciales_malas()",
        '    if not usuario.is_active:\n        raise HTTPException(status_code=403, detail="Tu cuenta esta dada de baja")',
        [API],
    ),
    (
        "el correo deja de normalizarse",
        AUTH,
        "    correo = datos.correo_normalizado",
        "    correo = datos.email",
        [API],
    ),
    (
        "quien entra deja de quedar registrado",
        AUTH,
        "    usuario.last_login_at = utcnow()\n    await db.commit()",
        "    await db.commit()",
        [API],
    ),
    # -----------------------------------------------------------------------
    # El limite de intentos
    # -----------------------------------------------------------------------
    (
        "el limite de intentos desaparece",
        AUTH,
        "    restantes = esta_bloqueado(correo, ip)\n    if restantes:\n        raise _demasiados_intentos(restantes)",
        "    restantes = 0",
        [API],
    ),
    (
        "los intentos fallidos no se cuentan",
        AUTH,
        '    if not verificar_contrasena(datos.password, usuario.password_hash):\n        bloqueados = anotar_intento_fallido(correo, ip)',
        '    if not verificar_contrasena(datos.password, usuario.password_hash):\n        bloqueados = 0',
        [API],
    ),
    (
        "acertar la contrasena deja el bloqueo puesto",
        LIMITE,
        "        _intentos.pop(clave, None)\n        _bloqueados.pop(clave, None)",
        "        _intentos.pop(clave, None)",
        [API],
    ),
    (
        "el bloqueo es global y no por cuenta",
        LIMITE,
        "def _clave(correo: str, ip: str) -> tuple[str, str]:\n    return correo.strip().lower(), ip",
        'def _clave(correo: str, ip: str) -> tuple[str, str]:\n    return ("todos-los-correos", ip)',
        [API],
    ),
    (
        "la ventana de intentos nunca expira",
        LIMITE,
        "        while cola and cola[0] <= ahora - VENTANA_SEGUNDOS:\n            cola.popleft()",
        "        pass",
        [API],
    ),
    (
        "la IP se ignora y el limite solo mira el correo",
        AUTH,
        '    cabecera = request.headers.get("x-forwarded-for", "")\n    if cabecera:\n        return cabecera.split(",")[0].strip()\n    return request.client.host if request.client else "desconocida"',
        '    return "la-misma-ip-para-todos"',
        [API],
    ),
    # -----------------------------------------------------------------------
    # La autoria: el "user" constante
    # -----------------------------------------------------------------------
    (
        "la revision vuelve a firmarse con la constante user",
        "app/api/tickets.py",
        "    ticket.reviewed_by = usuario.email",
        '    ticket.reviewed_by = "user"',
        [AUDIT],
    ),
    (
        "el veredicto del muestreo vuelve a no tener dueno",
        "app/api/tickets.py",
        "    row.spot_checked_by = usuario.email",
        '    row.spot_checked_by = "user"',
        [AUDIT],
    ),
    # -----------------------------------------------------------------------
    # El arranque: que se niegue a levantar con una clave mala
    # -----------------------------------------------------------------------
    (
        "en produccion se inventa una clave en vez de negarse a arrancar",
        CONFIG,
        '            if self.ENVIRONMENT.lower() == "production":\n                raise ValueError(',
        "            if False:\n                raise ValueError(",
        [CONFIG_T],
    ),
    (
        "una clave corta se acepta en produccion",
        CONFIG,
        "        elif len(self.SECRET_KEY) < 32:\n            raise ValueError(",
        "        elif False:\n            raise ValueError(",
        [CONFIG_T],
    ),
    (
        "una clave corta se acepta en desarrollo",
        CONFIG,
        "        elif len(self.SECRET_KEY) < 32:\n            raise ValueError(",
        "        elif len(self.SECRET_KEY) < 0:\n            raise ValueError(",
        [CONFIG_T],
    ),
    (
        "la clave autogenerada avisa en silencio",
        CONFIG,
        '            logger.warning(\n                "SECRET_KEY no esta definida: se genero una clave de un solo uso "',
        '            _ = (\n                "SECRET_KEY no esta definida: se genero una clave de un solo uso "',
        [CONFIG_T],
    ),
    (
        "la clave autogenerada es la misma en todos los procesos",
        CONFIG,
        "            self.SECRET_KEY = secrets.token_urlsafe(48)",
        '            self.SECRET_KEY = "clave-fija-que-esta-en-el-repositorio-000"',
        [CONFIG_T],
    ),
    # -----------------------------------------------------------------------
    # El coste del hash. Subir N es facil; subirlo sin que nada lo compruebe es
    # como se vuelve a bajar sin querer.
    # -----------------------------------------------------------------------
    (
        "el coste de scrypt vuelve al que no recomienda la guia",
        SEGURIDAD,
        "_SCRYPT_N = 2**17",
        "_SCRYPT_N = 2**14",
        ["tests/unit/test_security.py::TestElCosteDeScrypt"],
    ),
    (
        "scrypt se queda sin paralelismo (2**14 con p=1 no esta en la lista)",
        SEGURIDAD,
        "_SCRYPT_P = 1",
        "_SCRYPT_P = 5",
        ["tests/unit/test_security.py::TestElCosteDeScrypt"],
    ),
    (
        "el techo de memoria se deja de calcular con los parametros del hash",
        SEGURIDAD,
        "    return 128 * r * n + 32 * 1024 * 1024",
        "    return 16 * 1024 * 1024",
        ["tests/unit/test_security.py::TestElCosteDeScrypt"],
    ),
    (
        "verificar usa los parametros del modulo y no los del hash",
        SEGURIDAD,
        "            n=int(n),\n            r=int(r),\n            p=int(p),",
        "            n=_SCRYPT_N,\n            r=_SCRYPT_R,\n            p=_SCRYPT_P,",
        [
            "tests/unit/test_security.py::TestElHashDeContrasena"
            "::test_el_hash_antiguo_se_sigue_validando_despues_de_subir_n",
        ],
    ),
    (
        "el techo de memoria se queda corto para un hash con N alto",
        SEGURIDAD,
        "    return 128 * r * n + 32 * 1024 * 1024",
        "    return _MAXMEM_SCRYPT",
        [
            "tests/unit/test_security.py::TestElCosteDeScrypt"
            "::test_un_hash_mas_caro_que_la_constante_tambien_verifica",
        ],
    ),
]


def _correr(objetivos: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *objetivos, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    return proc.returncode != 0, proc.stdout + proc.stderr


def main() -> int:
    print("Verificacion por mutacion de la sesion\n")
    supervivientes: list[str] = []

    base_murio, salida = _correr(["tests/"])
    if base_murio:
        print("  La suite ya falla antes de mutar. Arregla eso primero:")
        print(salida[-2500:])
        return 1
    print("  Base: suite en verde\n")

    with tempfile.TemporaryDirectory() as temporal:
        for nombre, archivo, original, mutado, objetivos in MUTACIONES:
            ruta = RAIZ / archivo
            if not ruta.exists():
                print(f"  ?? {nombre}: no existe {archivo}")
                supervivientes.append(nombre)
                continue

            fuente = ruta.read_text(encoding="utf-8")
            if original not in fuente:
                print(f"  ?? {nombre}: el patron original no aparece en {archivo}")
                print("     (el codigo cambio; la mutacion esta obsoleta)")
                supervivientes.append(nombre)
                continue

            copia = Path(temporal) / f"{len(supervivientes)}-{nombre.replace(' ', '_')[:40]}-{ruta.name}"
            shutil.copy2(ruta, copia)

            def _restaurar(*_args, _ruta=ruta, _copia=copia) -> None:
                """Deja el archivo como estaba, pase lo que pase.

                Sin esto, un Ctrl-C o un timeout del harness a mitad de una
                mutacion deja el codigo de produccion roto en el arbol de
                trabajo, con el comentario diciendo una cosa y el codigo
                haciendo otra.
                """
                try:
                    if _ruta.exists() and _copia.exists():
                        shutil.copy2(_copia, _ruta)
                except OSError:
                    pass

            atexit.register(_restaurar)
            for _senal in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(_senal, lambda s, f: (f(), sys.exit(1)))
                except ValueError:
                    pass

            try:
                ruta.write_text(fuente.replace(original, mutado, 1), encoding="utf-8")
                murio, salida = _correr(objetivos)
            finally:
                shutil.copy2(copia, ruta)
                atexit.unregister(_restaurar)

            if murio:
                print(f"  murio       {nombre}")
            else:
                supervivientes.append(nombre)
                print(f"  SOBREVIVIO  {nombre}")
                print(f"              ningun test de {', '.join(objetivos)} lo nota")

    print()
    if supervivientes:
        print(f"{len(supervivientes)} de {len(MUTACIONES)} mutaciones sobrevivieron:")
        for nombre in supervivientes:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. Los tests miran lo que dicen mirar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
