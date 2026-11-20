"""Schemas de la carga de gastos por lote.

`Item` y `ExpenseReceiptSchema` vivian en el modulo plano `app/schemas.py`,
que hoy queda opacado por el paquete `app/schemas/`: Python resuelve
`app.schemas` al paquete primero, asi que ese archivo no se puede importar.
Se mueven aqui para que `from app.schemas import ExpenseReceiptSchema` vuelva
a significar algo.

`ExpenseReceiptSchema` es el contrato estrecho de la lectura de IA: comprueba
que el total cuadra con las partidas. NO es la respuesta de la API. La carga
por lote devuelve `ScanItemResponse`, que trae los datos **juntos al veredicto**
del gate; devolver solo estos campos dejaria a quien los lee creyendo que el
sistema respondio por ellos cuando el gate los mando a revision.
"""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.schemas.scan import ScanItemResponse


class Item(BaseModel):
    name: str
    price: Decimal
    quantity: int

    @field_validator("price", "quantity")
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class ExpenseReceiptSchema(BaseModel):
    country: str
    comercio: str
    identificacion_fiscal: str
    fecha: str
    items: List[Item]
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    metodo_pago: str
    link_o_folio_facturacion: Optional[str] = None
    hash_duplicado: str

    @model_validator(mode="after")
    def total_must_match_items(self) -> "ExpenseReceiptSchema":
        calculated_subtotal = sum(item.price * item.quantity for item in self.items)
        expected_total = calculated_subtotal + self.tax
        # Tolerancia de centavos para evitar errores de redondeo de Decimal.
        if abs(self.total - expected_total) > Decimal("0.05"):
            raise ValueError(
                f"Total general ({self.total}) no coincide con la suma de items "
                f"y tax ({expected_total})"
            )
        return self


class ArchivoNoPermitido(BaseModel):
    """Un archivo de la carpeta que NO se mando a leer, con su nombre.

    El nombre va tal cual, y no el sha256 que usa `scan_files`: el sha256 es la
    clave de dedup, no algo que una persona pueda buscar en su carpeta. Sin
    esta lista, un directorio con 20 PDF y un `.docx` responderia como si todo
    hubiera salido bien, y el `.docx` seria el unico archivo que nadie mira.
    """

    nombre: str
    extension: str
    motivo: str


class DigitalizacionDirectorioResponse(BaseModel):
    """Resultado de leer una carpeta, con los rechazados en su propio campo.

    Los permitidos van en `archivos` con el MISMO `ScanItemResponse` del
    escanner: datos del papel y veredicto del gate en el mismo objeto. Poner
    los rechazados dentro de esa lista mezclaria "se leyo y salio asi" con
    "no se leyo porque no estaba permitido", que son dos preguntas distintas.
    """

    carpeta: str
    extensiones_permitidas: list[str]
    archivos: list[ScanItemResponse]
    # Los nombres originales, en el MISMO orden que `archivos`.
    # `ScanItemResponse.relative_path` es un sha256 para que el dedup no
    # dependa del nombre, y un sha256 no le dice a nadie que archivo mirar.
    # Va aparte para no ensuciar el schema compartido del escaner.
    nombres: list[str]
    # Formato decidido por los BYTES, en el mismo orden que `nombres`. El API
    # elige el lector con esto y no con la extension; sin este campo esa
    # decision inteligente seria invisible para quien opera la pantalla.
    formatos: list[str]
    archivos_no_permitidos: list[ArchivoNoPermitido]
    archivos_omitidos_por_tope: int = 0
