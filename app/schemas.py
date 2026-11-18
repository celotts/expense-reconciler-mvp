from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

class Item(BaseModel):
    name: str
    price: Decimal
    quantity: int

    @field_validator('price', 'quantity')
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Must be greater than zero')
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

    @model_validator(mode='after')
    def total_must_match_items(self) -> 'ExpenseReceiptSchema':
        calculated_subtotal = sum(item.price * item.quantity for item in self.items)
        expected_total = calculated_subtotal + self.tax
        # Tolerancia de centavos para evitar errores de redondeo de punto flotante/Decimal
        if abs(self.total - expected_total) > Decimal('0.05'):
            raise ValueError(f'Total general ({self.total}) no coincide con la suma de items y tax ({expected_total})')
        return self
