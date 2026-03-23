import hashlib
import json
from datetime import datetime
from pathlib import Path


def utc_now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def stable_hash_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def cache_read_enveloped(path, expected_source=None, default_data=None):
    """
    Supports both:
    1) New envelope format:
       {"schema_version": 1, "updated_at": "...", "source": "...", "data": ...}
    2) Legacy raw payloads (treated as data directly)
    """
    if default_data is None:
        default_data = {}

    p = Path(path)
    if not p.exists():
        return default_data, False

    try:
        payload = json.loads(p.read_text())
    except Exception:
        return default_data, True

    if isinstance(payload, dict) and "data" in payload and "schema_version" in payload:
        if expected_source and payload.get("source") != expected_source:
            return default_data, True
        return payload.get("data", default_data), False

    # Legacy cache content.
    return payload, False


def cache_write_enveloped(path, source, data, schema_version=1):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": schema_version,
        "updated_at": utc_now_iso(),
        "source": source,
        "data": data,
    }
    p.write_text(json.dumps(envelope, indent=2))


def write_json(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2))
