"""Application configuration loaded from environment variables (.env supported)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = <repo>/  (this file lives at backend/app/config.py)
BASE_DIR = Path(__file__).resolve().parents[2]

# Load .env from project root if present (does not override existing env vars)
load_dotenv(BASE_DIR / ".env")

INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = BASE_DIR / "backend" / "app" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

FRONTEND_DIR = BASE_DIR / "frontend"


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "SamadhanSetu")
    env: str = os.getenv("APP_ENV", "development")
    debug: bool = _bool("APP_DEBUG", "false")
    base_url: str = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")

    database_url: str = os.getenv("DATABASE_URL") or f"sqlite:///{INSTANCE_DIR / 'samadhansetu.db'}"

    # Security
    secret_key: str = os.getenv("SECRET_KEY", "")
    session_expire_hours: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "72") or 72)
    cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "smds_session")
    cookie_secure: bool = _bool("COOKIE_SECURE", "false")
    cookie_samesite: str = os.getenv("COOKIE_SAMESITE", "lax")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)) or 5242880)

    # Groq (primary LLM provider; optional — local rules always available)
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_enabled: bool = _bool("GROQ_ENABLED", "true")
    groq_timeout: float = _float("GROQ_TIMEOUT_SECONDS", 20.0)
    groq_max_retries: int = int(os.getenv("GROQ_MAX_RETRIES", "2") or 2)

    # Deterministic matching weights (must sum to 1.0)
    match_weight_skill: float = _float("MATCH_WEIGHT_SKILL", 0.40)
    match_weight_experience: float = _float("MATCH_WEIGHT_EXPERIENCE", 0.20)
    match_weight_interest: float = _float("MATCH_WEIGHT_INTEREST", 0.15)
    match_weight_availability: float = _float("MATCH_WEIGHT_AVAILABILITY", 0.15)
    match_weight_diversity: float = _float("MATCH_WEIGHT_DIVERSITY", 0.10)

    demo_mode: bool = True  # seeded demonstration data is always labelled
    seed_on_startup: bool = _bool("SEED_ON_STARTUP", "true")

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key) and self.groq_enabled

    @property
    def match_weights(self) -> dict[str, float]:
        return {
            "skill": self.match_weight_skill,
            "experience": self.match_weight_experience,
            "interest": self.match_weight_interest,
            "availability": self.match_weight_availability,
            "diversity": self.match_weight_diversity,
        }

    def safe_secret(self) -> str:
        """Return the configured secret key, or a random ephemeral one."""
        return self.secret_key or secrets.token_urlsafe(48)


settings = Settings()
