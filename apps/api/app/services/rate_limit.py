"""Atomic, database-backed fixed-window quotas, shared across API processes."""
import hashlib
import time
from sqlalchemy import delete, insert, update
from sqlalchemy.exc import IntegrityError
from app.db.database import SessionLocal
from app.models.hosted import RateBucket


def admit(subject: str, category: str, limit: int, seconds: int = 60) -> bool:
    now = int(time.time())
    bucket = hashlib.sha256(f'{subject}:{category}:{now // seconds}'.encode()).hexdigest()
    table = RateBucket.__table__
    with SessionLocal() as db:
        # Expired buckets are bounded by the current rate of traffic; no scheduled cleanup needed.
        db.execute(delete(table).where(table.c.expires < now))
        changed = db.execute(update(table).where(table.c.id == bucket, table.c.count < limit).values(count=table.c.count + 1))
        if changed.rowcount:
            db.commit()
            return True
        try:
            db.execute(insert(table).values(id=bucket, count=1, expires=(now // seconds + 1) * seconds))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            changed = db.execute(update(table).where(table.c.id == bucket, table.c.count < limit).values(count=table.c.count + 1))
            db.commit()
            return bool(changed.rowcount)
