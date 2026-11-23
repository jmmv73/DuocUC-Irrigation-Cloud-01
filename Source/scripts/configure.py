#!/usr/bin/env python3
"""Configure the three-container HTTP + MQTT classroom demo.

The only input is config/settings.json. No DNS, certificates, password files,
reverse proxy or AWS are required. Commands:
  render  -- update .env, Mosquitto configuration, Arduino connection.h
  init    -- render and start Docker Compose
  apply   -- render and update the running Docker Compose project
  validate -- validate settings.json, with no generated files or Docker calls
"""
import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config/settings.json"
ENV = ROOT / ".env"
GENERATED = ROOT / "generated"
STATE = GENERATED / "state.json"
ARDUINO_HEADER = ROOT / "arduino/SmartIrrigationUnoR4/connection.h"
SENSORS = ("soil_moisture", "temperature", "soil_ec", "water_ph")


def error(message):
    raise ValueError(message)


def load_config():
    with SETTINGS.open(encoding="utf-8") as fp:
        return json.load(fp)


def validate(c):
    hostname = c.get("public_host", "")
    if not isinstance(hostname, str) or hostname in ("YOUR_DROPLET_IP", "", "localhost"):
        error("Set public_host to your Droplet's actual public IPv4 address (or DNS hostname)")
    try:
        address = ipaddress.ip_address(hostname)
        if address.version != 4:
            error("Use an IPv4 address or DNS hostname for this Arduino sketch")
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", hostname):
            error("Invalid public_host: set an IPv4 address or DNS hostname")
        # Single labels are allowed for local lab tests; public deploy needs reachable address.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", c["device_id"]):
        error("device_id must be alphanumeric/dash/underscore")
    d, m, a = c["dashboard"], c["mqtt"], c["alarm"]
    for key, value in (("dashboard.port", d["port"]), ("mqtt.port", m["port"])):
        if type(value) is not int or not 1024 <= value <= 65535:
            error(f"{key} must be a TCP port between 1024 and 65535")
    if d["port"] == m["port"]:
        error("dashboard.port and mqtt.port must be different")
    if not isinstance(d["title"], str) or not 1 <= len(d["title"]) <= 90:
        error("dashboard.title must have 1-90 characters")
    for k, lower, upper in (("refresh_ms", 2000, 60000), ("history_hours", 1, 720), ("max_graph_points", 100, 20000)):
        if type(d[k]) is not int or not lower <= d[k] <= upper:
            error(f"dashboard.{k} out of range")
    if type(a["stale_seconds"]) is not int or not 15 <= a["stale_seconds"] <= 86400:
        error("alarm.stale_seconds out of range")
    if type(a["check_interval_seconds"]) is not int or not 5 <= a["check_interval_seconds"] <= 300:
        error("alarm.check_interval_seconds out of range")
    topics = m["topics"]
    if set(topics) != {"telemetry", "alarms", "thresholds"}:
        error("mqtt.topics must contain exactly telemetry, alarms, thresholds")
    paths = list(topics.values())
    if len(set(paths)) != 3 or any(
        not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_/-]{1,180}", t)
        or t.startswith("/") or t.endswith("/") or "//" in t
        for t in paths
    ):
        error("MQTT topics must be distinct paths without wildcard characters")
    sim = c.get("simulator", {})
    if not isinstance(sim, dict):
        error("simulator must be an object")
    if sim:
        if sim.get("mode") not in ("normal", "dry", "high-ph", "hot", "high-ec", "mixed", "cycle"):
            error("simulator.mode is not a recognized scenario")
        if type(sim.get("samples")) is not int or not 0 <= sim["samples"] <= 100000:
            error("simulator.samples must be 0-100000 (0 means continuous)")
        interval = sim.get("interval_seconds")
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 0.2 <= interval <= 3600:
            error("simulator.interval_seconds must be between 0.2 and 3600 seconds")
    rules = a["thresholds"]
    if set(rules) != set(SENSORS):
        error("Provide min/max thresholds for all four sensors")
    for name, r in rules.items():
        if set(r) != {"min", "max"}:
            error(f"{name} requires min and max")
        low, high = r["min"], r["max"]
        if any(type(n) not in (float, int) or not math.isfinite(n) for n in (low, high)) or low >= high:
            error(f"{name}: min must be less than max, numeric and finite")
        if name == "soil_moisture" and not (0 <= low < high <= 100):
            error("soil_moisture thresholds must be between 0 and 100")
        if name == "water_ph" and not (0 <= low < high <= 14):
            error("water_ph thresholds must be between 0 and 14")
        if name == "soil_ec" and low < 0:
            error("soil_ec thresholds cannot be negative")
    return c


def save_if_changed(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".updating-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def render(c):
    # Mosquitto 2 requires an explicit listener for non-local access.
    # WARNING: anonymous, unencrypted, demonstrator-only MQTT.
    mosquitto_conf = """listener 1883 0.0.0.0
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
connection_messages true
message_size_limit 4096
"""
    env_text = f"MQTT_PORT={c['mqtt']['port']}\nDASHBOARD_PORT={c['dashboard']['port']}\n"
    header_text = f'''#pragma once
// AUTO-GENERATED from config/settings.json. Do not edit directly.
// Re-run: python3 scripts/configure.py render
#define MQTT_HOST {json.dumps(c["public_host"])}
#define MQTT_PORT {c["mqtt"]["port"]}
#define MQTT_TOPIC_TELEMETRY {json.dumps(c["mqtt"]["topics"]["telemetry"])}
#define MQTT_TOPIC_ALARMS {json.dumps(c["mqtt"]["topics"]["alarms"])}
#define MQTT_TOPIC_THRESHOLDS {json.dumps(c["mqtt"]["topics"]["thresholds"])}
'''
    changed_broker = save_if_changed(GENERATED / "mosquitto/mosquitto.conf", mosquitto_conf)
    changed_env = save_if_changed(ENV, env_text, 0o600)
    save_if_changed(ARDUINO_HEADER, header_text)
    return changed_broker, changed_env


def docker(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True)


def apply(c, reset_thresholds=False):
    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    prev_digest = old.get("settings_sha256")
    digest = hashlib.sha256(SETTINGS.read_bytes()).hexdigest()
    broker_changed, env_changed = render(c)
    docker("config", "--quiet")
    docker("up", "-d", "--build")
    # Bind-mounted files do not automatically cause Docker Compose recreation.
    if prev_digest is not None and (broker_changed or env_changed):
        docker("restart", "broker")
    if prev_digest is not None and prev_digest != digest:
        docker("restart", "dashboard")
    if reset_thresholds:
        docker("exec", "-T", "dashboard", "python", "-c",
               "import core; core.reset_thresholds_from_config(); print('Initial thresholds restored in MongoDB')")
        # The exec process has its own (disconnected) MQTT client. Restart the
        # running dashboard so its on_connect callback republishes live state.
        docker("restart", "dashboard")
    save_if_changed(STATE, json.dumps({"settings_sha256": digest}, indent=2) + "\n", 0o600)
    print(f"Dashboard: http://{c['public_host']}:{c['dashboard']['port']}")
    print(f"MQTT broker: {c['public_host']}:{c['mqtt']['port']} (TCP, no TLS)")
    print("If you changed MQTT topics, copy the generated Arduino connection.h and re-upload the sketch.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", nargs="?", choices=("validate", "render", "init", "apply"), default="apply")
    p.add_argument("--reset-thresholds", action="store_true")
    args = p.parse_args()
    try:
        c = validate(load_config())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        p.error(f"Invalid config/settings.json: {exc}")
    if args.action == "validate":
        print("Configuration valid")
    elif args.action == "render":
        render(c)
        print(f"Generated Docker environment, Mosquitto config and Arduino header ({ARDUINO_HEADER}).")
    else:
        apply(c, reset_thresholds=args.reset_thresholds)


if __name__ == "__main__":
    main()
