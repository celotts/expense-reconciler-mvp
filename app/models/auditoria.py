from sqlalchemy import Column, BigInteger, String, DateTime, Text, UUID
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.sql import func
from app.core.database import Base


class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    rol = Column(String(50), nullable=True)
    accion = Column(String(100), nullable=False)
    modulo = Column(String(50), nullable=True)
    registro_afectado = Column(UUID(as_uuid=True), nullable=True)
    detalles = Column(Text, nullable=True)
    ip_inet = Column(String(45), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"Auditoria(id={self.id}, usuario={self.usuario_id}, accion={self.accion}, modulo={self.modulo}, fecha={self.fecha})"
