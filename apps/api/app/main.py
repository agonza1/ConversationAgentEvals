import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

# Load the repository .env before app.db.database constructs its engine.
from app.config import settings as _settings
from app.hosted_middleware import HostedBoundary, validate_hosted_configuration
from app.security import hosted
validate_hosted_configuration()
from app.db.database import Base, engine
from app.models import hosted as _hosted_models
from app.integrations.assert_runtime import ensure_expected_version
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.user_scenario_store import ensure_user_scenarios_registered

# Register native optional benchmark extensions before route modules bind service
# functions. This makes the scenario discoverable through the public catalog APIs
# while keeping it outside the core suite's default coverage denominator.
register_builtin_benchmark_extensions()
# File-backed user-created scenarios merge into the same catalog (_SUITES_BY_ID).
if not hosted():
    ensure_user_scenarios_registered()

from app.routes.assert_judge import router as assert_judge_router
from app.routes.chatgpt_plan import router as chatgpt_plan_router
from app.routes.agents import router as agents_router
from app.routes.benchmarks import router as benchmarks_router
from app.routes.bootstrap import router as bootstrap_router
from app.routes.decks import router as decks_router
from app.routes.execution import router as execution_router
from app.routes.product import router as product_router
from app.routes.realtime import router as realtime_router
from app.routes.scenarios import router as scenarios_router
from app.routes.sessions import router as sessions_router
from app.routes.specs import router as specs_router
from app.routes.waylo_connection import router as waylo_connection_router
from app.services.agent_store import ensure_seeded as ensure_agents_seeded

if not hosted():
    ensure_agents_seeded()
ensure_expected_version()

Base.metadata.create_all(bind=engine)

# Existing local rows remain unowned. A hosted login never inherits operator data.
with engine.begin() as connection:
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        columns = {column['name'] for column in inspector.get_columns(table.name)}
        if 'tenant_id' not in columns:
            connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN tenant_id VARCHAR(128)'))


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
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def private_validation_errors(request, exc):
    if hosted():
        # Pydantic's default input echo can expose malformed API keys or ID tokens.
        return JSONResponse(status_code=422, content={'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()
        ]})
    return await request_validation_exception_handler(request, exc)


app.add_middleware(HostedBoundary, router=app.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ['CAE_PUBLIC_ORIGIN']] if hosted() else ['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(assert_judge_router)
from app.routes.auth import router as auth_router
app.include_router(auth_router)
app.include_router(chatgpt_plan_router)
app.include_router(decks_router)
app.include_router(sessions_router)
app.include_router(realtime_router)
app.include_router(bootstrap_router)
app.include_router(benchmarks_router)
app.include_router(scenarios_router)
app.include_router(execution_router)
app.include_router(agents_router)
app.include_router(product_router)
app.include_router(specs_router)
app.include_router(waylo_connection_router)

BASE_DIR = Path(__file__).resolve().parents[3]
STORAGE_DIR = BASE_DIR / 'storage'
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/storage', StaticFiles(directory=str(STORAGE_DIR)), name='storage')


@app.get('/health')
def health_check():
    return {'status': 'ok'}
