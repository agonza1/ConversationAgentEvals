from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

API_DIR = Path(__file__).resolve().parents[2]
API_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = API_DIR / 'sales_presenter.db'
DATABASE_URL = f'sqlite:///{DB_PATH.resolve()}'

engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
