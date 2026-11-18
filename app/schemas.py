from pydantic import BaseModel, Field, validator
from decimal import Decimal

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

class Order(BaseModel):
    items: list[Item]
    total: Decimal

    @validator('total')
    def total_must_match_items(cls, v, values):
        if v != sum(item.price * item.quantity for item in values['items']):
            raise ValueError('Total must match items')
        return v
