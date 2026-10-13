"""Authenticated, atomic ingestion of derived averages over Supabase RPC."""
import os
from urllib.parse import urlsplit


def write_averages(summary, session=None):
    import requests

    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co") or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.port:
        raise ValueError("Invalid Supabase project URL")
    if not key.startswith("sb_secret_"):
        raise ValueError("A backend Supabase secret key is required")
    if not summary["parameters"]:
        raise ValueError("No valid numeric parameters")
    payload = {"p_site_code": summary["siteCode"], "p_captured_at": summary["capturedAt"],
               "p_station_count": summary["stationCount"], "p_parameters": summary["parameters"]}
    response = (session or requests).post(base + "/rest/v1/rpc/store_mmf_capture",
        headers={"apikey": key, "Content-Type": "application/json"}, json=payload,
        timeout=(10, 30), allow_redirects=False)
    try:
        if response.status_code != 200:
            raise RuntimeError("Supabase write failed")
        result = response.json()
        if not isinstance(result, dict) or result.get("parameterCount") != len(summary["parameters"]):
            raise RuntimeError("Supabase write was not confirmed")
    finally:
        response.close()
