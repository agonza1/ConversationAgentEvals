from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.db.database import Base, engine
from app.routes.benchmarks import router as benchmarks_router
from app.routes.bootstrap import router as bootstrap_router
from app.routes.decks import router as decks_router
from app.routes.evals import router as evals_router
from app.routes.realtime import router as realtime_router
from app.routes.sessions import router as sessions_router

Base.metadata.create_all(bind=engine)


def _ensure_session_columns() -> None:
    inspector = inspect(engine)
    if 'presentation_sessions' not in inspector.get_table_names():
        return

    columns = {column['name'] for column in inspector.get_columns('presentation_sessions')}
    migrations = {
        'autoplay_enabled': "ALTER TABLE presentation_sessions ADD COLUMN autoplay_enabled BOOLEAN NOT NULL DEFAULT 0",
        'autoplay_interval_seconds': "ALTER TABLE presentation_sessions ADD COLUMN autoplay_interval_seconds INTEGER NOT NULL DEFAULT 8",
        'autoplay_started_at': "ALTER TABLE presentation_sessions ADD COLUMN autoplay_started_at DATETIME",
    }

    with engine.begin() as connection:
        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(text(statement))


def _ensure_deck_columns() -> None:
    inspector = inspect(engine)
    if 'decks' not in inspector.get_table_names():
        return

    columns = {column['name'] for column in inspector.get_columns('decks')}
    migrations = {
        'manifest_json': "ALTER TABLE decks ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'",
    }

    with engine.begin() as connection:
        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(text(statement))


_ensure_session_columns()
_ensure_deck_columns()

app = FastAPI(title='Live Sales AI Presenter API', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(decks_router)
app.include_router(sessions_router)
app.include_router(realtime_router)
app.include_router(bootstrap_router)
app.include_router(evals_router)
app.include_router(benchmarks_router)

BASE_DIR = Path(__file__).resolve().parents[3]
STORAGE_DIR = BASE_DIR / 'storage'
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/storage', StaticFiles(directory=str(STORAGE_DIR)), name='storage')


@app.get('/health')
def health_check():
    return {'status': 'ok'}
