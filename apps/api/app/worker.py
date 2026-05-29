from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.benchmark_service import list_suites

HEALTH_FILE = Path(os.getenv('WORKER_HEALTH_FILE', '/tmp/conversation-agent-evals-worker-health'))
POLL_INTERVAL_SECONDS = max(5, int(os.getenv('WORKER_POLL_INTERVAL_SECONDS', '30')))
_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def _check_database() -> None:
    with SessionLocal() as db:
        db.execute(text('SELECT 1'))


def _check_seed_catalog() -> None:
    suites = list_suites()
    if len(suites) < 4:
        raise RuntimeError(f'expected at least 4 benchmark suites, found {len(suites)}')


def _write_health() -> None:
    HEALTH_FILE.write_text(str(time.time()))


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print('ConversationAgentEvals worker started.', flush=True)
    while _running:
        _check_database()
        _check_seed_catalog()
        _write_health()
        time.sleep(POLL_INTERVAL_SECONDS)
    print('ConversationAgentEvals worker stopped.', flush=True)


if __name__ == '__main__':
    run()
