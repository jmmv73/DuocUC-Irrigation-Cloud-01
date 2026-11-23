#!/usr/bin/env python3
"""Simulate Arduino UNO R4 WiFi over ordinary MQTT (no TLS).

Reads the same config/settings.json used by Docker and the Arduino sketch.
Sends four sensor values and listens to alarm/threshold feedback.

Examples:
  python tests/simulator.py --mode cycle --samples 15 --interval 3
  python tests/simulator.py --mode mixed --samples 8
  python tests/simulator.py --fault        # backward-compatible alarm test
  python tests/simulator.py --host 127.0.0.1 --mode normal
"""
import argparse
import json
import random
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    raise SystemExit("Install the MQTT client first: python -m pip install paho-mqtt==2.1.0") from exc

from sim_data import MODES, make_sample

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
defaults = config.get("simulator", {})

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--host", default=config["public_host"], help="Droplet IPv4/DNS, or 127.0.0.1 on the Droplet")
parser.add_argument("--port", type=int, default=config["mqtt"]["port"], help="Unencrypted MQTT TCP port")
parser.add_argument("--mode", choices=MODES, default=defaults.get("mode", "cycle"), help="Generated telemetry scenario")
parser.add_argument("--fault", action="store_true", help="Alias for --mode mixed (for earlier lab instructions)")
parser.add_argument("--samples", type=int, default=defaults.get("samples", 15), help="Number of samples, 0 = run continuously")
parser.add_argument("--interval", type=float, default=defaults.get("interval_seconds", 5.0), help="Seconds between samples")
parser.add_argument("--seed", type=int, default=None, help="Make sensor-value jitter reproducible")
args = parser.parse_args()

if args.samples < 0:
    parser.error("--samples must be 0 (continuous) or a positive number")
if args.interval < 0.2:
    parser.error("--interval must be at least 0.2 seconds")
if not 1 <= args.port <= 65535:
    parser.error("--port is not valid")
if args.host == "YOUR_DROPLET_IP":
    parser.error("Update config/settings.json public_host or use --host DROPLET_IP")

mode = "mixed" if args.fault else args.mode
rng = random.Random(args.seed)
run_id = uuid.uuid4().hex[:12]
ready = threading.Event()
feedback_count = 0

client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"sim-{uuid.uuid4().hex[:12]}",
    protocol=mqtt.MQTTv311,
    clean_session=True,
)
client.reconnect_delay_set(min_delay=1, max_delay=10)


def on_connect(c, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print(f"[MQTT] Connection rejected: {reason_code}", flush=True)
        return
    print("[MQTT] Connected to broker (plain MQTT, port %s)" % args.port, flush=True)
    for label in ("alarms", "thresholds"):
        c.subscribe(config["mqtt"]["topics"][label], qos=1)
    ready.set()


def on_disconnect(c, userdata, disconnect_flags, reason_code, properties):
    ready.clear()
    print(f"[MQTT] Disconnected: {reason_code}", flush=True)


def on_message(c, userdata, msg):
    global feedback_count
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        feedback_count += 1
        if msg.topic == config["mqtt"]["topics"]["alarms"]:
            states = ", ".join(f"{key}={value}" for key, value in data.get("states", {}).items())
            print(f"[RX alarms] active={data.get('active')} stale={data.get('stale')} | {states}", flush=True)
        else:
            print(f"[RX thresholds] {json.dumps(data, ensure_ascii=False)}", flush=True)
    except (ValueError, UnicodeError) as exc:
        print(f"[RX] Invalid response on {msg.topic}: {exc}", flush=True)


client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

print("Smart Irrigation Simulator", flush=True)
print(f"Broker: {args.host}:{args.port} | mode={mode} | samples={args.samples or 'continuous'} | interval={args.interval}s", flush=True)
print(f"Telemetry topic: {config['mqtt']['topics']['telemetry']}", flush=True)

try:
    client.connect(args.host, args.port, keepalive=60)
except OSError as exc:
    sys.exit(f"Could not connect to the broker: {exc}. Check host, port and firewall.")

client.loop_start()
try:
    if not ready.wait(timeout=12):
        sys.exit("MQTT connection unavailable. Check Droplet IP, TCP port 1883 and container status.")

    index = 0
    while args.samples == 0 or index < args.samples:
        if not ready.wait(timeout=12):
            sys.exit("Lost MQTT connection and could not reconnect within 12 seconds")
        try:
            scenario, payload = make_sample(config, mode, index, rng, run_id)
        except ValueError as exc:
            parser.error(str(exc))

        info = client.publish(
            config["mqtt"]["topics"]["telemetry"],
            json.dumps(payload),
            qos=1,
            retain=False,
        )
        info.wait_for_publish(timeout=8)
        if not info.is_published():
            sys.exit("MQTT publish not acknowledged; check connectivity")

        print(f"[TX #{index+1} {scenario}] " +
              " ".join(f"{k}={payload[k]}" for k in ("soil_moisture", "temperature", "soil_ec", "water_ph")) +
              f" | sample_id={payload['sample_id']} | QoS1 ACK", flush=True)
        index += 1
        # Do not add an unnecessary sleep after the last finite sample.
        if args.samples == 0 or index < args.samples:
            time.sleep(args.interval)

    # Give the backend a brief opportunity to send its final alarm response.
    time.sleep(1.0)
    print(f"Simulation complete. MQTT feedback messages observed: {feedback_count}.", flush=True)
except KeyboardInterrupt:
    print("\nSimulation interrupted.", flush=True)
finally:
    client.disconnect()
    client.loop_stop()
