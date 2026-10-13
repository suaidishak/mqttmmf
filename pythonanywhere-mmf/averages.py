"""Derived cross-station arithmetic means; raw messages are never modified."""
from datetime import datetime
import math
import re

METADATA = frozenset({"timestamp", "time", "type", "unit", "units", "quality", "status"})
PARAMETER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def measurement(value):
    unit = None
    if isinstance(value, dict):
        unit = value.get("unit")
        value = value.get("value")
    if unit is not None and (not isinstance(unit, str) or len(unit) > 64):
        unit = None
    return numeric(value), unit


def average_snapshot(snapshot):
    """One latest valid value per station/parameter, never one per message."""
    grouped = {}
    excluded = 0
    unverified = 0
    for station in snapshot["stations"]:
        latest = {}
        for message in station["messages"]:
            # Retained or old values must not be averaged as fresh weather.
            age = (datetime.fromisoformat(snapshot["snapshotAt"]) - datetime.fromisoformat(message["receivedAt"])).total_seconds()
            if message["retained"] or not 0 <= age <= snapshot["intervalSeconds"]:
                unverified += 1
                continue
            topic = message["topic"].split("/")
            station_index = topic.index(station["stationCode"]) if station["stationCode"] in topic else None
            leaf = topic[station_index + 1:] if station_index is not None else []
            value = message["value"]
            if leaf:
                # The final topic component is the source parameter code.
                candidates = [(leaf[-1], value)]
            elif isinstance(value, dict):
                candidates = [(key, val) for key, val in value.items() if key.lower() not in METADATA]
            else:
                continue
            for parameter, raw in candidates:
                if not PARAMETER.fullmatch(parameter):
                    excluded += 1
                    continue
                number, unit = measurement(raw)
                if number is None:
                    excluded += 1
                    continue
                # Receipt order wins; direct-topic data wins ties over mirrors.
                rank = (datetime.fromisoformat(message["receivedAt"]), bool(leaf), message["topic"])
                previous = latest.get(parameter)
                if previous is None or rank > previous[0]:
                    latest[parameter] = (rank, number, unit)
        for parameter, (_, number, unit) in latest.items():
            grouped.setdefault(parameter, []).append((number, unit))
    parameters = []
    conflicts = []
    for parameter, samples in sorted(grouped.items()):
        units = {unit for _, unit in samples if unit is not None}
        if len(units) > 1:
            conflicts.append(parameter)
        # Divide before summing so equal very large finite readings don't overflow.
        count = len(samples)
        mean = math.fsum(number / count for number, _ in samples)
        if not math.isfinite(mean):
            excluded += count
            continue
        parameters.append({"parameterCode": parameter, "averageValue": mean,
                           "contributingStations": count, "unit": next(iter(units)) if len(units) == 1 else None,
                           "sourceUnits": sorted(units), "unitConflict": len(units) > 1,
                           "stationsWithUnit": sum(unit is not None for _, unit in samples)})
    return {"siteCode": snapshot["siteCode"], "capturedAt": snapshot["snapshotAt"],
            "stationCount": len(snapshot["stations"]), "aggregationMethod": "cross_station_arithmetic_mean",
            "timestampBasis": "collector_receipt_utc", "parameters": parameters,
            "excludedValues": excluded, "unverifiedMessages": unverified, "unitConflicts": conflicts}
