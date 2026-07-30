import os
from pathlib import Path

from dotenv import load_dotenv

# Explicit path rather than relying on cwd: scripts/tests run from
# backend/, but the .env lives at the repo root next to .env.example.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _normalize_database_url(url: str) -> str:
    """Render (and most managed Postgres providers) hand out
    postgres://... or postgresql://..., but psycopg2 needs the
    dialect+driver form SQLAlchemy expects."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


class Settings:
    ticker: str = os.getenv("VOL_SURFACE_TICKER", "SPY")
    rate_ticker: str = os.getenv("VOL_SURFACE_RATE_TICKER", "^IRX")

    database_url: str = _normalize_database_url(
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://vol_user:vol_pass@localhost:5432/vol_surface",
        )
    )

    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    # liquidity filters applied before Black-Scholes inversion
    min_open_interest: int = int(os.getenv("MIN_OPEN_INTEREST", "1"))
    max_relative_spread: float = float(os.getenv("MAX_RELATIVE_SPREAD", "0.5"))
    min_dte: int = int(os.getenv("MIN_DTE", "2"))  # exclude 0DTE/1DTE, too noisy

    # Black-Scholes IV inversion bounds
    iv_lower_bound: float = 1e-4
    iv_upper_bound: float = 5.0


settings = Settings()
