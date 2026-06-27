"""Environment-backed configuration. Reads .env from the repo root."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from pencil_extract.paths import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    email: str
    password: str
    timezone: str

    @classmethod
    def from_env(cls) -> "Config":
        email = os.environ.get("PENCIL_EMAIL", "").strip()
        password = os.environ.get("PENCIL_PASSWORD", "")
        if not email or not password:
            raise ConfigError(
                "PENCIL_EMAIL and PENCIL_PASSWORD must be set in .env. "
                "Copy .env.example to .env and fill them in."
            )
        return cls(
            email=email,
            password=password,
            timezone=os.environ.get("TIMEZONE", "Europe/Madrid"),
        )
