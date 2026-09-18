import shutil
import time
from datetime import datetime
from pathlib import Path

from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from .tasks import WORKER_HEARTBEAT_KEY


def _memory_stats():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return {"total_bytes": None, "available_bytes": None, "used_percent": None}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    used_percent = (
        round((total - available) / total * 100, 2)
        if total and available is not None
        else None
    )
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": used_percent,
    }


def _disk_stats():
    try:
        usage = shutil.disk_usage("/app")
    except OSError:
        usage = shutil.disk_usage("/")
    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "used_percent": round(usage.used / usage.total * 100, 2) if usage.total else None,
    }


def infrastructure_health():
    database = {"ok": False, "latency_ms": None}
    started = time.monotonic()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database = {
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }
    except Exception:
        database["latency_ms"] = round((time.monotonic() - started) * 1000, 2)

    cache_state = {"ok": False, "latency_ms": None}
    cache_started = time.monotonic()
    try:
        probe = f"infra-probe:{time.time_ns()}"
        cache.set(probe, "ok", timeout=10)
        cache_state = {
            "ok": cache.get(probe) == "ok",
            "latency_ms": round((time.monotonic() - cache_started) * 1000, 2),
        }
        cache.delete(probe)
    except Exception:
        cache_state["latency_ms"] = round((time.monotonic() - cache_started) * 1000, 2)

    heartbeat_raw = cache.get(WORKER_HEARTBEAT_KEY)
    heartbeat_at = None
    heartbeat_age_seconds = None
    if heartbeat_raw:
        try:
            heartbeat_at = datetime.fromisoformat(str(heartbeat_raw))
            if timezone.is_naive(heartbeat_at):
                heartbeat_at = timezone.make_aware(heartbeat_at)
            heartbeat_age_seconds = max(
                0,
                round((timezone.now() - heartbeat_at).total_seconds(), 1),
            )
        except (TypeError, ValueError):
            heartbeat_at = None
    background_ok = heartbeat_age_seconds is not None and heartbeat_age_seconds <= 150

    memory = _memory_stats()
    disk = _disk_stats()
    warnings = []
    if memory["used_percent"] is not None and memory["used_percent"] >= 90:
        warnings.append("Использование оперативной памяти выше 90%")
    if disk["used_percent"] is not None and disk["used_percent"] >= 90:
        warnings.append("Использование диска выше 90%")

    critical_ok = bool(database["ok"] and cache_state["ok"] and background_ok)
    return {
        "ok": critical_ok and not warnings,
        "critical_ok": critical_ok,
        "warnings": warnings,
        "database": database,
        "cache": cache_state,
        "background_tasks": {
            "ok": background_ok,
            "last_heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": heartbeat_age_seconds,
        },
        "memory": memory,
        "disk": disk,
        "generated_at": timezone.now(),
    }
