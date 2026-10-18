"""Hosted identity and request-local state. Never change process environment per user."""
from __future__ import annotations

import os
from contextvars import ContextVar
from collections.abc import MutableMapping
from copy import deepcopy
from typing import Any
from contextlib import nullcontext

identity: ContextVar[str | None] = ContextVar('cae_identity', default=None)
request_maps: ContextVar[dict | None] = ContextVar('cae_request_maps', default=None)
request_db: ContextVar[Any] = ContextVar('cae_request_db', default=None)


FIREBASE_CONFIG_VARS = (
    'FIREBASE_PROJECT_ID', 'FIREBASE_WEB_API_KEY', 'FIREBASE_AUTH_DOMAIN', 'FIREBASE_WEB_APP_ID',
)


def hosted() -> bool:
    mode = os.getenv('CAE_AUTH_MODE', '')
    if mode:
        return mode == 'firebase'
    # Any partial configuration must enter validation, never silently disable auth.
    return any(os.getenv(name) for name in FIREBASE_CONFIG_VARS)


def user_id() -> str:
    value = identity.get()
    if not value:
        raise PermissionError('An authenticated user is required.')
    return value


class ScopedCatalog(MutableMapping):
    """Local catalogs stay global; hosted mutations exist only in the request context."""
    def __init__(self, name: str, initial: dict):
        self.name, self.local = name, initial
        self.builtin = deepcopy(initial)

    def _data(self):
        if not hosted():
            return self.local
        maps = request_maps.get()
        if maps is None:
            # Startup must not import a local user's scenarios into hosted catalogs.
            return self.builtin
        return maps.setdefault(self.name, deepcopy(self.builtin))

    def __getitem__(self, key): return self._data()[key]
    def __setitem__(self, key, value): self._data()[key] = value
    def __delitem__(self, key): del self._data()[key]
    def __iter__(self): return iter(self._data())
    def __len__(self): return len(self._data())


class PrivateDocuments(MutableMapping):
    """SQL-backed hosted records, with the existing dictionary for local development."""
    def __init__(self, kind: str):
        self.kind, self.local = kind, {}

    def __getitem__(self, key):
        if not hosted(): return self.local[key]
        value = document_get(self.kind, str(key))
        if value is None: raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        if not hosted(): self.local[key] = value
        else: document_put(self.kind, str(key), value)

    def __delitem__(self, key):
        if not hosted(): del self.local[key]
        else:
            from app.db.database import SessionLocal
            from app.models.hosted import PrivateDocument
            with nullcontext(request_db.get()) if request_db.get() is not None else SessionLocal() as db:
                count = db.query(PrivateDocument).filter_by(kind=self.kind, key=str(key)).delete()
                if request_db.get() is None: db.commit()
                if not count: raise KeyError(key)

    def __iter__(self):
        if not hosted(): return iter(self.local)
        from app.db.database import SessionLocal
        from app.models.hosted import PrivateDocument
        with nullcontext(request_db.get()) if request_db.get() is not None else SessionLocal() as db:
            return iter([row.key for row in db.query(PrivateDocument).filter_by(kind=self.kind)])

    def __len__(self): return len(list(iter(self)))


def document_get(kind: str, key: str, *, independent: bool = False) -> Any:
    import json
    from app.db.database import SessionLocal
    from app.models.hosted import PrivateDocument
    session = None if independent else request_db.get()
    with nullcontext(session) if session is not None else SessionLocal() as db:
        row = db.query(PrivateDocument).filter_by(kind=kind, key=key).first()
        return json.loads(row.value) if row else None


def document_put(kind: str, key: str, value: Any, *, independent: bool = False) -> None:
    import json
    from app.db.database import SessionLocal
    from app.models.hosted import PrivateDocument
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    session = None if independent else request_db.get()
    with nullcontext(session) if session is not None else SessionLocal() as db:
        insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
        statement = insert(PrivateDocument).values(tenant_id=user_id(), kind=kind, key=key, value=json.dumps(value))
        db.execute(statement.on_conflict_do_update(index_elements=['tenant_id', 'kind', 'key'], set_={'value': statement.excluded.value}))
        if session is None: db.commit()
