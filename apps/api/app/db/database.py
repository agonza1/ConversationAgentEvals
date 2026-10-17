import os
from pathlib import Path

from sqlalchemy import Column, String, create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker, with_loader_criteria
from app.security import hosted, request_db, user_id

API_DIR = Path(__file__).resolve().parents[2]
API_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = API_DIR / 'sales_presenter.db'
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DB_PATH.resolve()}')

engine_kwargs = {}
if DATABASE_URL.startswith('sqlite'):
    engine_kwargs['connect_args'] = {'check_same_thread': False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
class TenantOwned:
    # NULL rows belong only to the pre-auth local installation, never to a hosted user.
    tenant_id = Column(String(128), nullable=True, index=True)


Base = declarative_base(cls=TenantOwned)


@event.listens_for(Session, 'do_orm_execute')
def _tenant_queries(state):
    if hosted() and state.is_orm_statement:
        uid = user_id()
        state.statement = state.statement.options(with_loader_criteria(
            TenantOwned, lambda model: model.tenant_id == uid, include_aliases=True,
        ))


@event.listens_for(Session, 'before_flush')
def _tenant_writes(session, _context, _instances):
    if not hosted(): return
    uid = user_id()
    for row in session.new | session.dirty | session.deleted:
        if isinstance(row, TenantOwned):
            if row in session.new:
                row.tenant_id = uid
            elif row.tenant_id != uid:
                raise PermissionError('Cross-user writes are forbidden.')


def get_db():
    if hosted() and request_db.get() is not None:
        yield request_db.get()
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
