from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.config import settings
from app.db.database import Base, engine
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.user_scenario_store import ensure_user_scenarios_registered

# Register native optional benchmark extensions before route modules bind service
# functions. This makes the scenario discoverable through the public catalog APIs
# while keeping it outside the core suite's default coverage denominator.
register_builtin_benchmark_extensions()
# File-backed user-created scenarios merge into the same catalog (_SUITES_BY_ID).
ensure_user_scenarios_registered()

from app.routes.assert_sidecar import router as assert_sidecar_router
from app.routes.agents import router as agents_router
from app.routes.benchmarks import router as benchmarks_router
from app.routes.bootstrap import router as bootstrap_router
from app.routes.decks import router as decks_router
from app.routes.execution import router as execution_router
from app.routes.product import router as product_router
from app.routes.realtime import router as realtime_router
from app.routes.scenarios import router as scenarios_router
from app.routes.sessions import router as sessions_router
from app.services.agent_store import ensure_seeded as ensure_agents_seeded

ensure_agents_seeded()

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


def _ensure_product_project_indexes() -> None:
    inspector = inspect(engine)
    if 'product_projects' not in inspector.get_table_names():
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_product_projects_user_project_key '
                'ON product_projects (user_id, project_key)'
            )
        )


def _ensure_product_project_columns() -> None:
    inspector = inspect(engine)
    if 'product_projects' not in inspector.get_table_names():
        return

    columns = {column['name'] for column in inspector.get_columns('product_projects')}
    migrations = {
        'workspace_id': 'ALTER TABLE product_projects ADD COLUMN workspace_id VARCHAR',
        'settings_json': "ALTER TABLE product_projects ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'",
        'onboarding_json': "ALTER TABLE product_projects ADD COLUMN onboarding_json TEXT NOT NULL DEFAULT '{}'",
    }

    with engine.begin() as connection:
        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(text(statement))


_ensure_session_columns()
_ensure_deck_columns()
_ensure_product_project_columns()
_ensure_product_project_indexes()

app = FastAPI(title='ConversationAgentEvals API', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

if settings.assert_local_sidecar_enabled:
    app.include_router(assert_sidecar_router)
app.include_router(decks_router)
app.include_router(sessions_router)
app.include_router(realtime_router)
app.include_router(bootstrap_router)
app.include_router(benchmarks_router)
app.include_router(scenarios_router)
app.include_router(execution_router)
app.include_router(agents_router)
app.include_router(product_router)

BASE_DIR = Path(__file__).resolve().parents[3]
STORAGE_DIR = BASE_DIR / 'storage'
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/storage', StaticFiles(directory=str(STORAGE_DIR)), name='storage')


@app.get('/health')
def health_check():
    return {'status': 'ok'}
