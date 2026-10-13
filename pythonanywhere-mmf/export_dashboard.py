"""Publish only owner-approved derived MMF averages; never raw MQTT messages."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

CAPTURE_LIMIT = 192
HISTORY_DAYS = 7
PARAMETER_LIMIT = 1024
PAGE_LIMIT = 1000
MAX_PAGES = 200
MAX_BYTES = 20 * 1024 * 1024
SITE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PARAMETER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parameter(row, station_count):
    code = row["parameter_code"]
    value = row["average_value"]
    count = row["contributing_stations"]
    coverage = row["stations_with_unit"]
    units = row["source_units"]
    unit = row["unit"]
    conflict = row["unit_conflict"]
    if not isinstance(code, str) or not PARAMETER.fullmatch(code):
        raise ValueError("Invalid parameter code")
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Invalid average")
    if type(count) is not int or not 1 <= count <= station_count or type(coverage) is not int or not 0 <= coverage <= count:
        raise ValueError("Invalid contribution count")
    if not isinstance(units, list) or len(units) > 64 or any(not isinstance(u, str) or len(u) > 64 for u in units):
        raise ValueError("Invalid units")
    if type(conflict) is not bool or conflict != (len(set(units)) > 1) or unit is not None and (not isinstance(unit, str) or len(unit) > 64):
        raise ValueError("Invalid unit metadata")
    # Exact allowlist: extra database fields, credentials and source payloads
    # cannot leak into the public file even if the input shape later expands.
    return {"parameterCode": code, "averageValue": value,
            "contributingStations": count, "unit": unit,
            "sourceUnits": sorted(set(units)), "unitConflict": conflict,
            "stationsWithUnit": coverage}


def assemble(site_code, captures, rows, now):
    if not SITE.fullmatch(site_code) or len(captures) > CAPTURE_LIMIT:
        raise ValueError("Invalid capture scope")
    grouped = {}
    for row in captures:
        if row["site_code"] != site_code or type(row["station_count"]) is not int or not 1 <= row["station_count"] <= 64:
            raise ValueError("Invalid capture")
        at = stamp(row["captured_at"])
        if at in grouped:
            raise ValueError("Duplicate capture")
        grouped[at] = {"capturedAt": at, "stationCount": row["station_count"], "parameters": []}
    seen = set()
    for row in rows:
        at = stamp(row["captured_at"])
        if row["site_code"] != site_code or at not in grouped:
            raise ValueError("Out-of-scope parameter")
        p = parameter(row, grouped[at]["stationCount"])
        key = (at, p["parameterCode"])
        if key in seen:
            raise ValueError("Duplicate parameter")
        seen.add(key)
        grouped[at]["parameters"].append(p)
    ordered = sorted(grouped.values(), key=lambda c: c["capturedAt"])
    for capture in ordered:
        if not 1 <= len(capture["parameters"]) <= PARAMETER_LIMIT:
            raise ValueError("Incomplete capture")
        capture["parameters"].sort(key=lambda p: p["parameterCode"])
    return {"schemaVersion": 1, "generatedAt": stamp(now.isoformat()),
            "historyDays": HISTORY_DAYS, "historyCaptureLimit": CAPTURE_LIMIT,
            "aggregationMethod": "cross_station_arithmetic_mean",
            "timestampBasis": "collector_receipt_utc",
            "sites": [{"siteCode": site_code, "captures": ordered}]}


def request_rows(session, base, key, table, params):
    response = session.get(base + "/rest/v1/" + table, params=params,
                           headers={"apikey": key, "Accept": "application/json"},
                           timeout=(10, 30), allow_redirects=False)
    try:
        if response.status_code != 200 or len(response.content) > MAX_BYTES:
            raise RuntimeError("Dashboard query failed")
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > PAGE_LIMIT:
            raise RuntimeError("Invalid query response")
        return rows
    finally:
        response.close()


def read_dashboard(session, base, key, site_code, now):
    start = stamp((now - timedelta(days=HISTORY_DAYS)).isoformat())
    captures = request_rows(session, base, key, "mmf_captures", [
        ("select", "site_code,captured_at,station_count"), ("site_code", "eq." + site_code),
        ("captured_at", "gte." + start), ("captured_at", "lte." + stamp(now.isoformat())),
        ("order", "captured_at.desc"), ("limit", str(CAPTURE_LIMIT))])
    if not captures:
        return assemble(site_code, [], [], now)
    newest = stamp(captures[0]["captured_at"])
    oldest = stamp(captures[-1]["captured_at"])
    rows = []
    cursor = None
    for _ in range(MAX_PAGES):
        params = [("select", "site_code,captured_at,parameter_code,average_value,contributing_stations,unit,source_units,unit_conflict,stations_with_unit"),
                  ("site_code", "eq." + site_code), ("captured_at", "gte." + oldest),
                  ("captured_at", "lte." + newest),
                  ("order", "captured_at.desc,parameter_code.asc"), ("limit", str(PAGE_LIMIT))]
        if cursor:
            at, code = cursor
            params.append(("or", f"(captured_at.lt.{at},and(captured_at.eq.{at},parameter_code.gt.{code}))"))
        page = request_rows(session, base, key, "mmf_parameter_averages", params)
        if not page:
            return assemble(site_code, captures, rows, now)
        last = page[-1]
        next_cursor = (stamp(last["captured_at"]), last["parameter_code"])
        if not isinstance(next_cursor[1], str) or not PARAMETER.fullmatch(next_cursor[1]) or cursor == next_cursor:
            raise RuntimeError("Invalid pagination cursor")
        rows.extend(page)
        if len(rows) > CAPTURE_LIMIT * PARAMETER_LIMIT:
            raise RuntimeError("Dashboard row limit exceeded")
        # Continue until an empty page, rather than trusting a full-page size:
        # a project may enforce a smaller REST row cap than PAGE_LIMIT.
        cursor = next_cursor
    raise RuntimeError("Dashboard pagination limit exceeded")


def main():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    site_code = os.environ.get("MMF_SITE_CODE", "LSS-BIDOR")
    url = urlsplit(base)
    if os.environ.get("MMF_DASHBOARD_PUBLIC_ENABLED") != "true":
        raise ValueError("Public publication is not enabled")
    if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".supabase.co") or url.path or url.query or url.fragment or url.username or url.port or not key.startswith("sb_secret_") or not SITE.fullmatch(site_code):
        raise ValueError("Invalid dashboard configuration")
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1, status_forcelist=[429, 502, 503, 504], allowed_methods=["GET"])))
        data = read_dashboard(session, base, key, site_code, datetime.now(timezone.utc))
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Dashboard publication too large")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    print(f"event=dashboard_exported captures={len(data['sites'][0]['captures'])} parameters={sum(len(c['parameters']) for c in data['sites'][0]['captures'])}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never print an exception containing credentials, URLs or payloads.
        print("event=dashboard_export_failed")
        raise SystemExit(1) from None
