from pydantic import BaseModel, Field, validator, model_validator
from decimal import Decimal
from typing import List, Optional

class Item(BaseModel):
    name: str
    price: Decimal
    quantity: int

    @validator('price')
    def price_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Price must be positive')
        return v

    @validator('quantity')
    def quantity_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Quantity must be positive')
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
    link_o_folio_facturacion: Optional[str]
    hash_duplicado: str

    @model_validator(mode='after')
    def total_must_match_items(cls, values):
        total_calculated = sum(item.price * item.quantity for item in values['items']) + values['tax']
        if not values['total'] == total_calculated:
            raise ValueError('Total must match items and tax')
        return values
