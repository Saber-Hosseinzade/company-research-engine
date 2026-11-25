"""Startup reachability check for the Supabase database (AgentCore spec R11).

Logs one line, `supabase reachable seconds=…` or `supabase unreachable error=<type>`, and never raises:
the check must never stop the app from starting. No Supabase data is read or written in this step.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import psycopg

log = logging.getLogger(__name__)

TIMEOUT = 10  # seconds to connect


def check(url: str, connect: Callable[..., Any] = psycopg.connect) -> bool:
    began = time.monotonic()
    try:
        with connect(url, connect_timeout=TIMEOUT, sslmode="require") as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # type only: a message can carry the host or user
        log.warning("supabase unreachable error=%s", type(exc).__name__)
        return False
    log.info("supabase reachable seconds=%.1f", time.monotonic() - began)
    return True


def check_configured() -> None:
    """Read the settings (and the app secret, when set) and check Supabase if it is configured."""
    try:
        from app.config import get_settings

        url = get_settings().supabase_database_url
    except Exception as exc:
        log.warning("supabase unreachable error=%s", type(exc).__name__)
        return
    if url is not None:
        check(url.get_secret_value())
