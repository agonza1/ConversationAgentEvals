from sqlalchemy import Column, Integer, String, Text
from app.db.database import Base


class PrivateDocument(Base):
    __tablename__ = 'private_documents'
    tenant_id = Column(String(128), primary_key=True)
    kind = Column(String(64), primary_key=True)
    key = Column(String(200), primary_key=True)
    value = Column(Text, nullable=False)


class RateBucket(Base):
    __tablename__ = 'rate_buckets'
    id = Column(String(64), primary_key=True)
    count = Column(Integer, nullable=False)
    expires = Column(Integer, nullable=False, index=True)
