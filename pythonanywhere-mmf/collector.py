"""Read-only MQTT subscriber; overwrite one private snapshot at UTC quarters."""
import argparse
import copy
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid
from urllib.parse import quote

STATIONS = tuple(f"MMF{i}" for i in range(1, 5))
INTERVAL = 900
MAX_PAYLOAD = 65536
MAX_TOPICS = 256
LOG = logging.getLogger("mmf")


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def next_boundary(timestamp):
    return (math.floor(timestamp / INTERVAL) + 1) * INTERVAL


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return validate_config(config)


def environment_config():
    tls = os.environ.get("MQTT_TLS", "false").lower()
    if tls not in ("true", "false"):
        raise ValueError("Invalid TLS setting")
    return validate_config({
        "host": os.environ.get("MQTT_HOST", ""),
        "port": int(os.environ.get("MQTT_PORT", "1883")),
        "tls": tls == "true",
        "siteCode": os.environ.get("MMF_SITE_CODE", "LSS-BIDOR"),
        "topicRoot": os.environ.get("MQTT_TOPIC_ROOT", "BDNS"),
        "pythonAnywhereUsername": os.environ.get("PYTHONANYWHERE_USERNAME", ""),
        "remoteFile": os.environ.get("PYTHONANYWHERE_REMOTE_FILE", ""),
    })


def validate_config(config):
    for key in ("host", "siteCode", "topicRoot", "pythonAnywhereUsername", "remoteFile"):
        if not isinstance(config.get(key), str) or not config[key] or "<" in config[key]:
            raise ValueError("Incomplete private configuration")
    root = config["topicRoot"]
    if any(c in root for c in "+#\x00") or root.startswith("/") or root.endswith("/"):
        raise ValueError("Invalid topic root")
    if type(config.get("port")) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("Invalid port")
    if type(config.get("tls")) is not bool:
        raise ValueError("Invalid TLS setting")
    username = config["pythonAnywhereUsername"]
    if not username.isalnum():
        raise ValueError("Invalid PythonAnywhere username")
    remote = config["remoteFile"]
    if not remote.startswith(f"/home/{username}/") or any(p in remote.split("/")[1:] for p in ("..", "sample_files", "")):
        raise ValueError("Invalid private remote path")
    for key in ("MQTT_USERNAME", "MQTT_PASSWORD", "PYTHONANYWHERE_API_TOKEN"):
        if not os.environ.get(key):
            raise ValueError("Missing runtime secret")
    return config


class Readings:
    def __init__(self, root, site_code):
        self.root = root
        self.site_code = site_code
        self.lock = threading.Lock()
        self.messages = {}
        self.connected = False

    def set_connected(self, connected):
        with self.lock:
            self.connected = connected

    def accept(self, topic, payload, retained=False, received_at=None):
        relative = topic.removeprefix(self.root + "/")
        station = relative.split("/", 1)[0]
        if not topic.startswith(self.root + "/") or station not in (*STATIONS, "MMF"):
            return False
        if station == "MMF" and relative != "MMF":
            return False
        if len(payload) > MAX_PAYLOAD or len(topic) > 512:
            return False
        try:
            raw = payload.decode("utf-8")
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError):
            return False
        if station == "MMF":
            if not isinstance(value, dict) or set(value) - set(STATIONS):
                return False
        record = {"topic": topic, "receivedAt": utc(received_at if received_at is not None else time.time()),
                  "retained": bool(retained), "rawPayload": raw, "value": value}
        with self.lock:
            if topic not in self.messages and len(self.messages) >= MAX_TOPICS:
                return False
            self.messages[topic] = record
        return True

    def snapshot(self, now):
        with self.lock:
            messages = copy.deepcopy(self.messages)
            connected = self.connected
        stations = []
        aggregate = messages.get(self.root + "/MMF")
        for station in STATIONS:
            prefix = self.root + "/" + station
            records = [v for k, v in sorted(messages.items()) if k == prefix or k.startswith(prefix + "/")]
            if aggregate and station in aggregate["value"]:
                records.append({**aggregate, "value": aggregate["value"][station], "aggregateStation": station})
            fresh = [r for r in records if not r["retained"] and 0 <= now - datetime.fromisoformat(r["receivedAt"]).timestamp() <= INTERVAL]
            status = "missing" if not records else "fresh" if fresh and connected else "staleOrUnverified"
            stations.append({"stationCode": station, "status": status, "messages": records})
        return {"siteCode": self.site_code, "snapshotAt": utc(now), "intervalSeconds": INTERVAL,
                "brokerConnected": connected, "dataKind": "liveOperationalSample",
                "timestampBasis": "receivedAt is collector receipt time; source timestamps are preserved only in payload",
                "stations": stations}


def upload_snapshot(config, snapshot, session=None):
    import requests

    username = quote(config["pythonAnywhereUsername"], safe="")
    remote = quote(config["remoteFile"], safe="/")
    url = f"https://www.pythonanywhere.com/api/v0/user/{username}/files/path{remote}"
    content = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    sender = session or requests
    # No redirects: the account token must stay on this fixed HTTPS origin.
    response = sender.post(url, headers={"Authorization": "Token " + os.environ["PYTHONANYWHERE_API_TOKEN"]},
                           files={"content": ("mmf_latest.json", content, "application/json")},
                           timeout=(10, 30), allow_redirects=False)
    try:
        if response.status_code not in (200, 201):
            raise RuntimeError("Upload failed")
    finally:
        response.close()


def deliver(config, snapshot, stop):
    # Retry the same snapshot; never retain a disk backlog of operational data.
    for delay in (0, 5, 20):
        if stop.wait(delay):
            return False
        try:
            upload_snapshot(config, snapshot)
            return True
        except Exception:
            LOG.warning("event=upload_attempt_failed requestId=%s", uuid.uuid4().hex)
    return False


def collect_once(config, readings, stop, seconds, no_upload=False):
    LOG.info("event=collection_window seconds=%s", seconds)
    if stop.wait(seconds):
        return 130
    snapshot = readings.snapshot(time.time())
    present = sum(bool(s["messages"]) for s in snapshot["stations"])
    retained = sum(any(m["retained"] for m in s["messages"]) for s in snapshot["stations"])
    LOG.info("event=collection_complete stationsWithData=%s stationsWithRetainedData=%s brokerConnected=%s", present, retained, snapshot["brokerConnected"])
    if not snapshot["brokerConnected"] or present != len(STATIONS):
        LOG.error("event=incomplete_collection upload=skipped")
        return 2
    if no_upload:
        LOG.info("event=probe_complete upload=disabled")
        return 0
    request_id = uuid.uuid4().hex
    if not deliver(config, snapshot, stop):
        LOG.error("event=snapshot_upload_failed requestId=%s", request_id)
        return 3
    LOG.info("event=snapshot_uploaded requestId=%s stationsWithData=%s", request_id, present)
    return 0


def run(config, once=False, seconds=180, no_upload=False):
    import paho.mqtt.client as mqtt

    readings = Readings(config["topicRoot"], config["siteCode"])
    LOG.info("event=collector_started intervalSeconds=%s", INTERVAL)
    stop = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mmf-" + uuid.uuid4().hex)
    client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ["MQTT_PASSWORD"])
    if config["tls"]:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    def on_connect(client, userdata, flags, reason_code, properties):
        readings.set_connected(False)
        if reason_code == 0:
            LOG.info("event=broker_connected")
            subscriptions = [(config["topicRoot"] + "/MMF", 1)]
            subscriptions.extend((config["topicRoot"] + "/" + station + "/#", 1) for station in STATIONS)
            client.subscribe(subscriptions)
        else:
            LOG.warning("event=broker_connection_rejected")

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        accepted = len(reason_codes) == 5 and all(not code.is_failure for code in reason_codes)
        readings.set_connected(accepted)
        LOG.info("event=subscription status=%s", "ready" if accepted else "rejected")

    def on_message(client, userdata, message):
        if not readings.accept(message.topic, message.payload, message.retain):
            LOG.warning("event=message_rejected requestId=%s", uuid.uuid4().hex)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    def disconnected(*args):
        readings.set_connected(False)
        LOG.warning("event=broker_disconnected")

    def connection_failed(*args):
        readings.set_connected(False)
        LOG.warning("event=broker_connection_failed retry=automatic")

    client.on_disconnect = disconnected
    client.on_connect_fail = connection_failed
    client.on_message = on_message
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *args: stop.set())
    client.connect_async(config["host"], config["port"], keepalive=60)
    client.loop_start()
    try:
        if once:
            return collect_once(config, readings, stop, seconds, no_upload)
        due = next_boundary(time.time())
        LOG.info("event=next_upload scheduledAt=%s", utc(due))
        while not stop.wait(min(max(due - time.time(), 0), 30)):
            now = time.time()
            if now < due:
                continue
            request_id = uuid.uuid4().hex
            try:
                snapshot = readings.snapshot(now)
                if deliver(config, snapshot, stop):
                    LOG.info("event=snapshot_uploaded requestId=%s stationsWithData=%s", request_id,
                             sum(bool(station["messages"]) for station in snapshot["stations"]))
                else:
                    LOG.error("event=snapshot_upload_failed requestId=%s", request_id)
            except (OSError, ValueError):
                LOG.error("event=snapshot_write_failed requestId=%s", request_id)
            due = next_boundary(time.time())
            LOG.info("event=next_upload scheduledAt=%s", utc(due))
    finally:
        client.disconnect()
        client.loop_stop()
        LOG.info("event=collector_stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="Absolute path to private config JSON")
    source.add_argument("--from-env", action="store_true", help="Read configuration and secrets from environment")
    parser.add_argument("--once", action="store_true", help="Collect a bounded window, upload once and exit")
    parser.add_argument("--collection-seconds", type=int, default=180, choices=range(1, 601), metavar="1..600")
    parser.add_argument("--no-upload", action="store_true", help="Probe station coverage without changing the remote file (requires --once)")
    parser.add_argument("--log-file", help="Private UTF-8 activity log, rotated at 2 MiB")
    args = parser.parse_args()
    if args.no_upload and not args.once:
        parser.error("--no-upload requires --once")
    handler = RotatingFileHandler(args.log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8") if args.log_file else logging.StreamHandler(sys.stdout)
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s UTC %(levelname)s %(message)s")
    logging.Formatter.converter = time.gmtime
    try:
        config = environment_config() if args.from_env else load_config(args.config)
        result = run(config, args.once, args.collection_seconds, args.no_upload)
        raise SystemExit(result or 0)
    except Exception:
        LOG.error("event=collector_failed requestId=%s", uuid.uuid4().hex)
        raise SystemExit(1) from None
