"""Los schemas del informe de cierre mensual.

**UN solo objeto, dos salidas.** El preview del front y el PDF salen de la misma
estructura (`ReporteCierreMensual`). No es una decision de estilo: si el JSON y
el PDF tuvieran cada uno su propia forma de presentar la exactitud, el contador
veria dos numeros distintos en la misma pantalla y la promesa entera del producto
—"esto es lo que el sistema leyo y esto es lo que una persona verifico"— se
caeria en la primera pantalla.

Por eso aqui no hay "el modelo del PDF": hay un modelo y dos consumidores.

**POR QUE EXISTE `schemas/reporte.py` Y NO ESTA EN OTRO SITIO.** El precedente mas
cercano es `TicketExtractionResult`, que vive en `parser_service.py:427` y no en
`schemas/ticket.py`, y que salio caro: el campo se agrego dos veces a
`TicketResponse` y ninguna a la clase que el escaner consumia. Un informe es un
contrato entre el backend y el PDF, y los dos lo tienen que encontrar. Va en su
modulo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field

from app.schemas.ticket import ReporteExactitudResponse

# Un porcentaje sin su intervalo de confianza al lado no significa nada (R1).
# Se repite aqui como constante de nombre para que el nombre aparezca donde se
# decide, y no solo en el comentario que lo explica.
SIN_INTERVALO = "el porcentaje va siempre con su intervalo de confianza al lado"


class ComparativoMes(BaseModel):
    """El mes anterior, cortado a la misma fecha que el periodo.

    `conclusivo=False` con `nota` explica por que, y es un estado de primera
    clase y no un campo opcional: un comparativo que no aplica tiene que DECIR
    que no aplica. Si `conclusivo` fuera `None`, el PDF tendria que adivinar, y
    lo que adivina un generador de PDF cuando no entiende un campo es imprimir
    la cifra sin la advertencia que la hacia verdadera.
    """

    nombre_anterior: str
    monto_anterior: Decimal
    tickets_anterior: int
    diferencia: Decimal | None = None
    diferencia_pct: float | None = None
    conclusivo: bool = True
    nota: str | None = None


class ResumenGasto(BaseModel):
    total: Decimal
    tickets: int
    por_categoria: list[dict] = Field(default_factory=list)
    comparativo: ComparativoMes | None = None


class SeccionConciliacion(BaseModel):
    """Como quedo la conciliacion del periodo, por estado real."""

    por_estado: dict[str, int] = Field(default_factory=dict)
    conciliados: int = 0
    sin_conciliar: int = 0
    monto_discrepancias: Decimal = Decimal("0.00")


class SeccionLectura(BaseModel):
    """R5: 'leido por el sistema' y 'verificado por una persona', jamas igual.

    Los dos numeros estan separados porque son afirmaciones de distinta fuerza, y
    ponerlos en un solo campo con una etiqueta ("revisados: 40") obliga a quien
    lee a suponer cual de los dos es. Aqui no hay que suponer: cada campo lleva
    su nombre y su etiqueta.
    """

    leido_por_el_sistema: int = 0
    verificado_por_una_persona: int = 0
    pendiente_de_revision: int = 0


class SeccionVerificacionHumana(BaseModel):
    """Lo que una persona reviso, con nombre y fecha.

    **Conteos y no porcentajes.** Un "% de la muestra firmado" es un porcentaje
    sin intervalo (R1), y el denominador de una muestra del 5% es chico: un 100%
    de 3 firmas es exactamente tan fragil como un 0% de 3. Los numeros sueltos no
    se pueden malinterpretar, y la proporcion se lee sola.
    """

    en_muestra: int = 0
    revisadas: int = 0
    firmadas: int = 0
    pendientes_de_firmar: int = 0
    revisores: list[str] = Field(default_factory=list)
    ultima_verificacion: date | None = None


class SeccionPendientes(BaseModel):
    """Lo que queda sin hacer. Es la seccion que decide si el periodo cierra."""

    sin_categoria_tickets: int = 0
    sin_categoria_monto: Decimal = Decimal("0.00")
    sin_conciliar_tickets: int = 0
    sin_conciliar_monto: Decimal = Decimal("0.00")
    discrepancias_abiertas: int = 0
    discrepancias_monto: Decimal = Decimal("0.00")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hay_pendientes(self) -> bool:
        return (
            self.sin_categoria_tickets > 0
            or self.sin_conciliar_tickets > 0
            or self.discrepancias_abiertas > 0
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def motivo(self) -> str | None:
        """Por que el periodo no se puede declarar cerrado (R3).

        Los tres motivos se nombran en la frase y no se summarize en un "hay
        pendientes": quien lee tiene que saber cual de los tres tiene que atacar
        primero, y el orden cambia el trabajo de la tarde.
        """
        partes: list[str] = []
        if self.sin_categoria_tickets:
            partes.append(f"{self.sin_categoria_tickets} sin categoria")
        if self.sin_conciliar_tickets:
            partes.append(f"{self.sin_conciliar_tickets} sin conciliar")
        if self.discrepancias_abiertas:
            partes.append(f"{self.discrepancias_abiertas} discrepancias abiertas")
        return ", ".join(partes) if partes else None


class ReporteCierreMensual(BaseModel):
    """El informe completo, en el orden del contrato (7 secciones).

    **NINGUN CAMPO DEPENDE DEL RELOJ.** No hay `datetime.now()` en ningun lado de
    este objeto, y eso es lo que hace posible R4: dos llamadas del mismo periodo
    sobre la misma base producen el MISMO objeto, y de ese objeto salen el JSON y
    el PDF. La fecha de referencia se deriva del periodo, y su nombre lo dice
    (`fecha_referencia`, no `emitido_en`) porque no es cuando se genero: es el
    periodo sobre el que se afirma.

    Un informe cuya fecha de emision fuera la hora de reloj seria mas comodo de
    leer y no valdria como evidencia: dos descargas del mismo mes differing en un
    minuto se leen como "alguien lo toco", y no hay forma de probar que no.
    """

    # --- 1. Identificacion ---
    empresa_id: str
    empresa_nombre: str
    empresa_tax_id: str
    periodo: str
    periodo_inicio: date
    periodo_fin: date
    fecha_referencia: date
    firmado_por: str

    # --- 2. Resumen del gasto ---
    gasto: ResumenGasto

    # --- 3. Hallazgos ---
    hallazgos: list[dict] = Field(default_factory=list)

    # --- 4. Estado de conciliacion ---
    conciliacion: SeccionConciliacion = Field(default_factory=SeccionConciliacion)

    # --- 5. Bloque de exactitud (el corazon) ---
    exactitud: ReporteExactitudResponse | None = None
    exactitud_disponible: bool = True
    # R2: si la evidencia no alcanza, esto va en la PRIMERA pagina.
    advertencia_principal: str | None = None

    # --- 6. Registro de verificacion humana ---
    lectura: SeccionLectura = Field(default_factory=SeccionLectura)
    verificacion: SeccionVerificacionHumana = Field(default_factory=SeccionVerificacionHumana)

    # --- 7. Pendientes ---
    pendientes: SeccionPendientes = Field(default_factory=SeccionPendientes)

    # --- R3 ---
    #
    # `cerrado`/`cerrado_por` son HECHOS: vienen de `cierres_periodo`. Se guardan
    # como campos porque son datos, no derivado.
    #
    # `puede_cerrarse` NO es un campo: se DERIVA de los pendientes. Antes lo era,
    # y eso era un segundo cubo con la misma verdad, que es exactamente como un
    # dia el PDF imprimio "el periodo NO se puede declarar cerrado: None": el
    # campo valia `False` mientras el motivo era `None`, y los dos se podian
    # desincronizar sin que nada lo delatara. Un campo derivado no puede mentir
    # sobre sus propias entradas.
    cerrado: bool = False
    cerrado_por: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def puede_cerrarse(self) -> bool:
        return not self.pendientes.hay_pendientes

    # `computed_field` y NO un `@property` pelado: un `@property` no lo serializa
    # `response_model`, asi que el front recibiria un JSON sin este estado y
    # tendria que recomputarlo — que es justo como el preview y el PDF empiezan a
    # discrepar entre si.
    #
    # CUATRO estados y no tres, y el cuarto se obtiene al escribir esto.
    #
    # `cerrado` (el hecho, leido de `cierres_periodo`) y `puede_cerrarse` (lo que
    # R3 permite afirmar HOY) pueden discrepar: alguien cerro el periodo y despues
    # se metio un ticket de ese mes. Con tres estados eso se reportaba como
    # `NO_CIERRA`, que es FALSO — el periodo SI esta cerrado en el registro— y de
    # paso escondia el hallazgo mas util que este informe puede dar: que se
    # escribio en un periodo ya cerrado.
    #
    # Por eso existe `CERRADO_CON_PENDIENTES`. No es un matiz: es la pregunta que
    # un contador le haria al sistema, respondida.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def estado_periodo(self) -> str:
        if self.cerrado and self.pendientes.hay_pendientes:
            return "CERRADO_CON_PENDIENTES"
        if self.cerrado:
            return "CERRADO"
        if not self.puede_cerrarse:
            return "NO_CIERRA"
        return "CIERRE_DISPONIBLE"