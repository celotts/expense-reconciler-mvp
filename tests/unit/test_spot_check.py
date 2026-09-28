"""Que el 96% sea un numero con respaldo, y no una cifra que nadie compro.

El objetivo de exactitud existia sin forma de verificarlo: nadie podia
responder de donde salia el 96%. Este modulo lo convierte en algo medible, y
casi todo lo que hace es negarse a decir cosas que no puede sostener:

- No reporta un porcentaje sin cuantos se revisaron. 24 de 25 y 480 de 500 dan
  el mismo 96% y no significan lo mismo; con 25 revisiones el intervalo real va
  de 80% a 99%, asi que publicar el 96% sin el 80% afirma una certeza que nadie
  midio.
- No promedia metodos de lectura. Un ticket leido con regex y uno leido con un
  modelo se cuentan por separado, porque mezclarlos sube el promedio con el
  metodo que casi no falla y esconde al que falla.
- No dice "cumple" ni "no cumple" cuando la muestra no alcanza. Dice
  "todavia no se puede saber" y cuantos faltan.

Los tests de aqui fijan esas tres negaciones. Los que fallan si alguien
optimiza el codigo para "devolver un numero mas limpio" son tan importantes
como los que fijan el comportamiento correcto.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.enums import SPOT_CHECK_RATE, SpotCheckStatus
from app.services.accuracy_service import (
    NIVEL_CONFIANZA,
    NIVEL_CONFIANZA_Z,
    ORIGENES_A_REPORTAR,
    SLO_EXACTITUD,
    FaltanMuestras,
    MedidaPorOrigen,
    Motivo,
    Veredicto,
    campo_mas_fallido,
    en_muestra,
    faltantes_para_afirmar,
    intervalo_wilson,
    veredicto,
)


def _hash(n: int) -> str:
    """Un hash de verdad, como el que produce la app."""
    return hashlib.sha256(str(n).encode()).hexdigest()


class TestLaMuestraSeEligeSinAzarNiConsentimiento:
    """La seleccion tiene que ser reproducible y no manipulable.

    Un `random()` seria mas corto. Se cambio por hash por tres razones
    concretas, y las tres se comprueban aqui: recargar el mismo archivo decide
    lo mismo, la eleccion la hace el contenido y no quien la pide, y no
    depende de un proceso que se reinicie.
    """

    def test_cerca_de_la_tasa_configurada(self):
        """Con 50 000 archivos, la fraccion observada se acerca a la pedida.

        No se exige exactitud: una fraccion de archivos es una muestra y tiene
        su propio error. Lo que se rechaza es un sesgo de un orden de magnitud,
        que es como se rompe esto cuando se usa un prefijo del hash.
        """
        total = 50_000
        seleccionados = sum(en_muestra(_hash(i)) for i in range(total))
        assert abs(seleccionados / total - SPOT_CHECK_RATE) < 0.003

    def test_el_mismo_archivo_decide_siempre_lo_mismo(self):
        """Recargar el mismo comprobante no lo saca de la muestra.

        Si la decision cambiara entre peticiones, el revisor veria un ticket
        aparecer y desaparecer de la cola, y la evidencia ya reunida dejaria de
        ser comparable con la nueva.
        """
        hash_real = _hash(7)
        assert len({en_muestra(hash_real) for _ in range(50)}) == 1

    def test_la_decicion_sobrevive_a_un_reinicio(self):
        """La eleccion no puede depender del `PYTHONHASHSEED` del proceso.

        `hash()` de Python esta sembrado por proceso: dos arranques darian
        decisiones distintas para el mismo archivo, y los tickets de una
        corrida mas nueva cairian en la muestra mientras los de la anterior
        siguen fuera. El historico de evidencia se parte en dos mitades que
        nunca se comparan.
        """
        codigo = (
            "import hashlib\n"
            "from app.services.accuracy_service import en_muestra\n"
            "print([en_muestra(hashlib.sha256(str(i).encode()).hexdigest())\n"
            "       for i in range(40)])\n"
        )
        raiz = Path(__file__).resolve().parents[2]
        corrida_1 = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True,
            cwd=raiz, env={"PYTHONHASHSEED": "0", "PATH": "/usr/bin:/bin"},
        )
        corrida_2 = subprocess.run(
            [sys.executable, "-c", codigo], capture_output=True, text=True,
            cwd=raiz, env={"PYTHONHASHSEED": "424242", "PATH": "/usr/bin:/bin"},
        )
        assert corrida_1.returncode == 0 and corrida_2.returncode == 0, corrida_1.stderr
        assert corrida_1.stdout == corrida_2.stdout

    def test_aguanta_hashes_degenerados(self):
        """Un hash con ceros al principio no manda a todo el mundo a la muestra.

        La version anterior tomaba los primeros 32 bits del hex. Con SHA-256
        eso es uniforme y funcionaba, pero dependia de una suposicion que no
        hace falta: si la forma de calcular el hash cambia o se degrada, un
        prefijo puede quedar sesgado y el muestreo se rompe al 100% o al 0%, en
        silencio. Con el entero completo no hay nada que suponer.
        """
        # Hashes de contador: todos empiezan con ceros, el caso patologico.
        seleccionados = sum(en_muestra(f"{i:064x}") for i in range(20_000))
        assert abs(seleccionados / 20_000 - SPOT_CHECK_RATE) < 0.01

    def test_la_captura_manual_no_entra_a_la_muestra(self):
        """Lo manual no es automatismo, y medirlo con la metrica del automatismo
        seria medir dos cosas distintas como si fueran una."""
        assert en_muestra(None) is False
        assert en_muestra("") is False

    def test_una_tasa_de_cero_no_manda_a_nadie(self):
        """Poder apagar el muestreo sin desplegar, por si el volumen es alto
        y las revisiones estan saturadas."""
        assert en_muestra(_hash(1), 0.0) is False

    def test_una_tasa_de_uno_manda_a_todo(self):
        """Y poder auditar el 100% sin cambiar el codigo, que es como se
        calibra el modelo antes de confiar en el."""
        assert en_muestra(_hash(1), 1.0) is True

    def test_un_hash_corrupto_no_revienta_la_carga(self):
        """Un valor que no es hex cae al respaldo estable.

        No deberia pasar nunca: `compute_source_hash` siempre produce hex. Pero
        si pasara, reventar la carga de un comprobante por eso seria peor que
        no muestrearlo.
        """
        assert en_muestra("no-soy-un-hash", 0.5) in (True, False)


class TestElIntervaloDiceLoQueLaMuestraSostiene:
    """Wilson, y por que no el intervalo normal.

    La eleccion del metodo no es academica aqui: este sistema vive en los
    extremos, con muestras chicas y aciertos casi perfectos, que es justo donde
    el intervalo normal se rompe y declara certezas que no existen.
    """

    def test_el_punto_medio_siempre_cae_dentro(self):
        """Propiedad basica: el estimador esta dentro de su propio intervalo.

        Se prueba en muchos tamanos, incluidos los extremos, porque un metodo
        puede fallar en un caso y no en el vecino.
        """
        for k, n in [(0, 1), (1, 1), (0, 25), (24, 25), (25, 25), (1, 100),
                     (97, 100), (485, 500), (500, 500), (50, 50), (9, 10)]:
            bajo, alto = intervalo_wilson(k, n)
            assert bajo <= k / n <= alto, f"el punto cae fuera en {k}/{n}"

    def test_un_intervalo_confianza_siempre_acota_a_cero_y_uno(self):
        """Ningun limite puede salirse del rango, ni por error de coma flotante."""
        for k, n in [(0, 1), (1, 1), (0, 25), (25, 25), (500, 500), (1, 1000)]:
            bajo, alto = intervalo_wilson(k, n)
            assert 0.0 <= bajo < alto <= 1.0, f"limites invalidos en {k}/{n}"

    def test_se_estrecha_al_crecer_la_muestra(self):
        """Mas evidencia, menos duda. Si el ancho no baja, la muestra no sirve
        de nada y el reporte deberia decirlo en vez significar el numero."""
        anchos = [
            intervalo_wilson(k, n)[1] - intervalo_wilson(k, n)[0]
            for k, n in [(50, 100), (50, 400), (50, 1600), (500, 16000)]
        ]
        assert all(anchos[i] > anchos[i + 1] for i in range(len(anchos) - 1))

    def test_los_extremos_no_salen_con_ruido_de_coma_flotante(self):
        """500 de 500 da 1.0 exacto, no 0.9999999999999999.

        No es un dato mas preciso, es ruido. Y se nota en pantalla: un
        "99.99999999999999%" hace que quien lo lee sospeche del numero entero en
        lugar de la precision. A media escala la precision si importa y no se
        toca; en el borde, se ajusta.
        """
        assert intervalo_wilson(500, 500)[1] == 1.0
        assert intervalo_wilson(0, 25)[0] == 0.0
        # Y el interior no se redondea por gusto.
        assert intervalo_wilson(485, 500)[0] != 0.95

    def test_sin_muestra_no_hay_intervalo(self):
        """Cero revisiones no es "cero a cien por ciento".

        Un intervalo que abarca todo no es un intervalo: es la ausencia de
        informacion. Devolverlo como si fuera un dato permitiria que un sistema
        sin ninguna revision se viera como el mas o menos preciso, y el
        reporte necesita distinguir las dos cosas.
        """
        assert intervalo_wilson(0, 0) is None

    def test_un_conteo_invalido_no_se_inventa_un_intervalo(self):
        """Mas aciertos que revisados es un error de datos, no un cero."""
        with pytest.raises(ValueError, match="fuera de rango"):
            intervalo_wilson(11, 10)


class TestUnPorcentajeSoloNoDiceNada:
    """El 5% de 500 tickets da 25 revisiones, y 25 no alcanzan.

    Esta clase fija el motivo por el que el reporte nunca devuelve un
    porcentaje pelado. Es la razon de ser del modulo: sin esto, el sistema
    reportaria "96%" con la misma seguridad con que reportaria "80%", y las dos
    cifras describen sistemas que no sirven igual.
    """

    def test_veinticinco_de_veinticinco_no_pueden_afirmar_nada(self):
        """Aun siendo perfecto, con 25 muestras no se puede decir que se
        llega al 96%. El limite inferior llega a 86.7%."""
        assert veredicto(25, 25) == Veredicto.INCONCLUYENTE

    def test_veinticuatro_de_veinticinco_tampoco(self):
        """Este es el caso peligroso: da exactamente 96.00%, el objetivo, y no
        se puede afirmar. Un reporte que mirara el punto medio publicaria el
        objetivo cumplido con la evidencia que lo respalda."""
        assert veredicto(24, 25) == Veredicto.INCONCLUYENTE

    def test_algo_mas_bajo_tampoco_se_puede_descartar_a_veces(self):
        """Con 92% y 25 muestras, el limite superior llega a 97.8%: no se
        puede descartar que el sistema cumpla. Es inconclusive, no malo."""
        assert veredicto(23, 25) == Veredicto.INCONCLUYENTE

    def test_un_fracaso_claro_si_se_puede_decir(self):
        """Cuando el limite superior cae por debajo del objetivo, ahi si se
        puede decir que no cumple, sin rodeos."""
        assert veredicto(20, 25) == Veredicto.NO_CUMPLE

    def test_sin_revisiones_el_veredicto_no_es_ninguno(self):
        """Un veredicto que no distingue "nadie ha mirado" de "nadie acerto"
        invita a leer lo segundo cuando lo que pasa es lo primero."""
        assert veredicto(0, 0) == Veredicto.SIN_EVIDENCIA

    def test_con_muestra_grande_si_se_puede_afirmar(self):
        """970 de 1000 no se pueden afirmar (hacen falta 1440), pero 1500 de
        1500 si. El metodo no esta polarizado hacia un lado."""
        assert veredicto(970, 1000) == Veredicto.INCONCLUYENTE
        assert veredicto(1500, 1500) == Veredicto.CUMPLE


class TestPorqueFaltaMuestra:
    """"Faltan N revisiones" y "es imposible" no son lo mismo.

    Se separaron en motivos distintos porque la accion es distinta. Si falta
    muestra, el trabajo es revisar. Si el acierto esta por debajo del objetivo,
    revisar mas no lo arregla: hay que cambiar el extractor. Un reporte que
    dijera "faltan mas revisiones" para los dos casos mandaria a la gente a
    medir un sistema roto, que es la forma mas lenta de no arreglarlo.
    """

    def test_sin_muestra_no_hay_cuenta_que_hacer(self):
        resultado = faltantes_para_afirmar(0, 0)
        assert resultado.razon == Motivo.SIN_MUESTRA
        assert resultado.total_necesario is None

    def test_por_debajo_del_objetivo_no_se_arranca_midiendo(self):
        resultado = faltantes_para_afirmar(80, 100)
        assert resultado.razon == Motivo.ACIERTO_POR_DEBAJO
        assert resultado.total_necesario is None

    def test_exactamente_en_el_objetivo_no_tiene_solucion(self):
        """El caso que mas confunde.

        Con 96.0% medidos, ningun numero de revisiones lo sube del 96.0%: el
        punto medio esta EN la linea, y el objetivo dice "mas de". El limite
        inferior se acerca a 96% por arriba del lado de adentro, pero nunca lo
        cruza. Con 10 000 revisiones el intervalo es [95.60%, 96.37%]: mas
        estrecho, igual de sin conclusion.
        """
        resultado = faltantes_para_afirmar(96, 100)
        assert resultado.razon == Motivo.ACIERTO_EN_LA_LINEA
        assert resultado.total_necesario is None
        for n in (100, 1000, 10_000, 100_000):
            assert veredicto(round(0.96 * n), n) == Veredicto.INCONCLUYENTE

    def test_un_numerito_redondo_no_acerca_la_solucion(self):
        """Devolver un numero grande y esperanzador cuando la respuesta es
        "nunca" es peor que devolver None: alguien lo leeria como "ya casi"."""
        resultado = faltantes_para_afirmar(96, 100)
        assert not resultado.es_posible
        assert resultado.total_necesario is None

    def test_con_acierto_por_encima_si_dice_cuantas_faltan(self):
        """Con 97% el numero es accionable: dice el total, no la diferencia."""
        resultado = faltantes_para_afirmar(970, 1000)
        assert resultado.razon == Motivo.SUFICIENTE
        assert resultado.total_necesario == 1440
        # El total, no las que faltan: 1440 es el tamaño de muestra, no 440.
        assert resultado.total_necesario > 1000

    def test_el_motivo_va_con_el_numero(self):
        """El reporte necesita la razon en palabras, no un codigo que tenga
        que traducir quien lo lee."""
        assert isinstance(faltantes_para_afirmar(970, 1000), FaltanMuestras)
        assert faltantes_para_afirmar(970, 1000).razon is not None


class TestLaMedidaDiceQueArreglar:
    """Un porcentaje de error no es accionable. "El 3% falla en la fecha" si."""

    def test_el_campo_que_mas_falla(self):
        campos = {"total_amount": 3, "expense_date": 19, "provider_name": 1}
        assert campo_mas_fallido(campos) == ("expense_date", 19)

    def test_sin_errores_no_hay_campo_que_culpar(self):
        """Un ticket correcto no tiene un campo mas fallido. Inventar uno seria
        apontar a algo que no fallo, que es la forma de arreglar lo que no
        esta roto."""
        assert campo_mas_fallido({}) is None

    def test_la_media_expone_las_cosas_que_el_reporte_necesita(self):
        """Los conteos se calculan una vez y las propiedades los derivan, para
        que el reporte no pueda usar un numero distinto al que se guardo."""
        medida = MedidaPorOrigen(
            origen="llm", revisados=100, aciertos=97, incorrectos=3,
            campos_fallidos={"expense_date": 3}, pendientes=12,
        )
        assert medida.exactitud == 0.97
        assert medida.veredicto == Veredicto.INCONCLUYENTE
        assert medida.campo_mas_fallido == ("expense_date", 3)
        assert medida.pendientes == 12
        assert medida.limites is not None

    def test_sin_revisar_no_hay_exactitud_pero_tampoco_cero(self):
        """La ausencia de un numero es "nadie miro", no "se fallo todo".
        Confundir las dos pone a un origen sin evidencia del lado del
        promedio, hundiendo el numero de todo el sistema."""
        medida = MedidaPorOrigen(origen="pdf_text")
        assert medida.exactitud is None
        assert medida.limites is None
        assert medida.veredicto == Veredicto.SIN_EVIDENCIA


class TestElEnumYLaBaseDicenLoMismo:
    """La lista de estados vive en dos sitios: Python y el DDL de Postgres.

    Divergen cuando se agrega un estado al enum y no a la constraint, y el
    error aparece al registrar la primera revision, en produccion, con un
    IntegrityError que no dice cual de los dos se quedo atras. Este test
    compara las dos listas leyendo el archivo de migracion.
    """

    def test_los_estados_del_enum_son_los_que_acepta_la_constraint(self):
        ruta = Path(__file__).resolve().parents[2] / "db/migrations/0003_spot_check.sql"
        assert ruta.is_file(), "falta la migracion 0003"
        sql = ruta.read_text(encoding="utf-8")

        encontrados = re.findall(r"'(PENDIENTE|CORRECTO|INCORRECTO)'", sql)
        del_enum = {e.value for e in SpotCheckStatus}

        assert set(encontrados) == del_enum, (
            "la migracion y el enum no listan los mismos estados: "
            f"en la migracion {set(encontrados)}, en el enum {del_enum}"
        )

    def test_los_estados_aparecen_en_la_constraint_de_valores(self):
        """No basta con que los tres nombres esten en el archivo: tienen que
        estar DENTRO de la constraint de valores, que es la que corre.

        Un nombre en un comentario no protege nada, y este archivo tiene
        comentarios largos que los mencionan a proposito.
        """
        ruta = Path(__file__).resolve().parents[2] / "db/migrations/0003_spot_check.sql"
        sql = ruta.read_text(encoding="utf-8")

        cuerpo = re.search(
            r"ADD CONSTRAINT ck_tickets_spot_check_values\s+CHECK\s*\((.*?)\);",
            sql, re.DOTALL,
        )
        assert cuerpo, "no se encontro la constraint ck_tickets_spot_check_values"
        dentro = set(re.findall(r"'([A-Z]+)'", cuerpo.group(1)))
        assert dentro == {e.value for e in SpotCheckStatus}

    def test_los_tres_estados_son_exactos(self):
        """Sin estado de "fuera de la muestra": esa fila tiene NULL.

        Un valor para el 95% de las filas haria crecer el indice de la cola con
        todo el historico y volveria indistinguible "no fue elegido" de
        "elegido y sin revisar".
        """
        assert {e.value for e in SpotCheckStatus} == {
            "PENDIENTE", "CORRECTO", "INCORRECTO",
        }


class TestLasConstantesDelReporteNoSeSeparan:
    """El nivel de confianza se publica y se calcula con el mismo numero.

    Si el reporte dijera 95% y el intervalo se calculara con otro z, las dos
    cosas serian la misma por coincidencia. Y si alguien ajustara uno, el
    reporte seguiria diciendo 95% mientras mostraria un intervalo de otro nivel,
    sin que nada fallara.
    """

    def test_el_z_corresponde_al_nivel_publicado(self):
        assert NIVEL_CONFIANZA == 0.95
        assert NIVEL_CONFIANZA_Z == pytest.approx(1.96, abs=0.005)

    def test_el_objetivo_es_el_acordado(self):
        """Va como constante y no como parametro de la API: si se pudiera
        pedir el reporte con cualquier objetivo, cada quien leeria el que le
        conviene."""
        assert SLO_EXACTITUD == 0.96

    def test_los_origenes_a_reportar_estan_fijos(self):
        """Un origen sin evidencia tiene que aparecer igual en el reporte.

        Si solo aparecieran los que tienen datos, la conclusion "el objetivo se
        cumple" se apoyaria en una base que nadie miro, y el origen sin
        revisar pasaria desapercibido justo cuando es el que hay que mirar.
        """
        assert set(ORIGENES_A_REPORTAR) == {"llm", "pdf_text", "rules"}


class TestElPeorVeredictoGana:
    """El veredicto global no es el promedio: es el peor de las vias.

    Promediar seria comfy y estaria mal. Un sistema que lee PDF impreso al
    100% y fotos al 88% no cumple el objetivo: hay una via donde casi una de
    cada diez se lee mal, y el promedio lo esconde detras de la via que nunca
    falla. Quien lea el promedio arregla lo que no esta roto.

    Estas comprobaciones viven aqui y no en el test de integracion porque la
    regla se decide sin tocar la base: sembrar 374 filas para alcanzar un
    CUMPLE de verdad en un test HTTP seria lento y no mediria nada del
    agregador, que ya se mide aqui con los cuatro casos.
    """

    def _medida(self, origen, veredicto_str, revisados=100, **kw):
        from app.api.tickets import ExactitudPorOrigenResponse

        return ExactitudPorOrigenResponse(
            origen=origen, revisados=revisados,
            aciertos=kw.get("aciertos", 0), incorrectos=0, pendientes=0,
            exactitud=kw.get("aciertos", 0) / revisados if revisados else None,
            veredicto=veredicto_str,
            motivo_faltante=kw.get("motivo", Motivo.SUFICIENTE),
            total_revisiones_necesarias=kw.get("necesarias"),
        )

    def test_un_cumple_no_lo_tapa_un_inconcluyente(self):
        """El caso que la integracion puede comprobar en corto: 30 aciertos no
        alcanzan a certificar, y 20 fallos si se ven. El global es el fallo."""
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("rules", Veredicto.INCONCLUYENTE, 100, aciertos=100),
            self._medida("llm", Veredicto.NO_CUMPLE, 100),
        ]
        assert _veredicto_global(medidas) == Veredicto.NO_CUMPLE

    def test_un_cumple_tampoco_lo_tapa_otro_cumple(self):
        """Este es el caso que importa de verdad, y el que la integracion no
        llegaba: una via que SI certifica el objetivo junto a otra que no.

        El promedio de las dos pasaria, con lo cual la conclusion seria
        "cumplimos" sobre un sistema que tiene una via rota.
        """
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("rules", Veredicto.CUMPLE, 2000, aciertos=2000),
            self._medida("llm", Veredicto.NO_CUMPLE, 200),
        ]
        assert _veredicto_global(medidas) == Veredicto.NO_CUMPLE

    def test_solo_cumple_si_ninguna_via_falla(self):
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("rules", Veredicto.CUMPLE, 2000, aciertos=2000),
            self._medida("pdf_text", Veredicto.CUMPLE, 2000, aciertos=1980),
        ]
        assert _veredicto_global(medidas) == Veredicto.CUMPLE

    def test_un_inconcluyente_no_puede_vencer_a_un_cumple(self):
        """Si algo no se puede medir, no se puede decir que se cumple. El
        destino neutro no es un si."""
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("rules", Veredicto.CUMPLE, 2000, aciertos=2000),
            self._medida("llm", Veredicto.INCONCLUYENTE, 25, aciertos=24),
        ]
        assert _veredicto_global(medidas) == Veredicto.INCONCLUYENTE

    def test_sin_ninguna_via_con_datos_no_se_concluye_nada(self):
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("llm", Veredicto.SIN_EVIDENCIA, 0),
            self._medida("rules", Veredicto.SIN_EVIDENCIA, 0),
        ]
        assert _veredicto_global(medidas) == Veredicto.SIN_EVIDENCIA

    def test_una_via_sin_datos_no_arrastra_a_las_que_si_tenen(self):
        """Un origen que nadie ha revisado no puede ser el veredicto global
        mientras otro ya tiene evidencia. Si no, bastaba no muestrear una via
        para subir el veredicto de todo el sistema a "sin evidencia"."""
        from app.api.tickets import _veredicto_global

        medidas = [
            self._medida("llm", Veredicto.SIN_EVIDENCIA, 0),
            self._medida("rules", Veredicto.CUMPLE, 2000, aciertos=2000),
        ]
        assert _veredicto_global(medidas) == Veredicto.CUMPLE


class TestLaExplicacionDiceQueHacer:
    """"INCONCLUYENTE" solo, sin texto, se lee como "algo fallo".

    Es la confusion mas probable de quien lee: un veredicto que significa
    "hace falta medir mas" y uno que significa "el sistema esta roto" se
    parecen en una etiqueta de tres palabras. La accion es opuesta en los dos
    casos, asi que el texto no es decorativo: es lo que evita que alguien vaya
    a medir un sistema que hay que arreglar.
    """

    def test_sin_datos_dice_que_no_se_afirma_nada(self):
        from app.api.tickets import ExactitudPorOrigenResponse, _explicacion_global

        medidas = [
            ExactitudPorOrigenResponse(
                origen="llm", revisados=0, aciertos=0, incorrectos=0, pendientes=0,
                exactitud=None, veredicto=Veredicto.SIN_EVIDENCIA,
                motivo_faltante=Motivo.SIN_MUESTRA,
            )
        ]
        texto = _explicacion_global(medidas)
        assert "no se puede afirmar" in texto
        assert "sin medirse" in texto, "tiene que distinguir de cumplirse"

    def test_cuando_falta_muestra_dice_cuantas_revisiones(self):
        from app.api.tickets import ExactitudPorOrigenResponse, _explicacion_global

        medidas = [
            ExactitudPorOrigenResponse(
                origen="llm", revisados=25, aciertos=25, incorrectos=0, pendientes=40,
                exactitud=1.0, veredicto=Veredicto.INCONCLUYENTE,
                motivo_faltante=Motivo.SUFICIENTE,
                total_revisiones_necesarias=374,
            )
        ]
        texto = _explicacion_global(medidas)
        assert "374" in texto
        assert "40" in texto, "el texto tiene que recordar lo que sigue pendiente"

    def test_cuando_el_acierto_esta_en_la_linea_dice_que_midir_no_ayuda(self):
        """El caso de 96.0% medidos. El texto tiene que decir que revisar mas
        no lo sube, porque la accion obvia (revisar mas) es la que no sirve."""
        from app.api.tickets import ExactitudPorOrigenResponse, _explicacion_global

        medidas = [
            ExactitudPorOrigenResponse(
                origen="llm", revisados=25, aciertos=24, incorrectos=1, pendientes=0,
                exactitud=0.96, veredicto=Veredicto.INCONCLUYENTE,
                motivo_faltante=Motivo.ACIERTO_EN_LA_LINEA,
            )
        ]
        texto = _explicacion_global(medidas)
        assert "extractor" in texto, "tiene que decir que hay que mejorarlo"
        assert "revisiones" in texto

    def test_cuando_no_cumple_dice_que_arreglar_el_extractor(self):
        from app.api.tickets import ExactitudPorOrigenResponse, _explicacion_global

        medidas = [
            ExactitudPorOrigenResponse(
                origen="llm", revisados=100, aciertos=70, incorrectos=30, pendientes=0,
                exactitud=0.7, veredicto=Veredicto.NO_CUMPLE,
                motivo_faltante=Motivo.ACIERTO_POR_DEBAJO,
            )
        ]
        texto = _explicacion_global(medidas)
        assert "corregir el extractor" in texto
        assert "Revisar mas no lo arregla" in texto
