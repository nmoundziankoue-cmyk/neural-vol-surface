from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import Base

# pool_pre_ping: managed Postgres (Render, RDS, etc.) drops idle
# connections server-side; without this, the first query after a pause
# fails with a stale-connection OperationalError instead of transparently
# reconnecting.
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
