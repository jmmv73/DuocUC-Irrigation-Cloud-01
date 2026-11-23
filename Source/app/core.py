"""MongoDB, validated MQTT ingestion, decision rules, outbound MQTT alarms."""
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

LOG = logging.getLogger("irrigation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

with open(os.getenv("DEVICE_CONFIG", "/app/config/settings.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)

DEVICE_ID = CONFIG["device_id"]
TOPICS = CONFIG["mqtt"]["topics"]
SENSORS = {
    "soil_moisture": ("Soil moisture", "%"),
    "temperature": ("Temperature", "°C"),
    "soil_ec": ("Soil conductivity", "mS/cm"),
    "water_ph": ("Irrigation water pH", "pH"),
}
LOCK = threading.RLock()

mongo_client = MongoClient(
    host=os.getenv("MONGO_HOST", "mongo"),
    port=27017,
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = mongo_client["riego"]
measurements = db["measurements"]
thresholds = db["thresholds"]
alarm_state = db["alarm_state"]
alarm_events = db["alarm_events"]

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"dashboard-{DEVICE_ID}",
    protocol=mqtt.MQTTv311,
    clean_session=True,
)
client.reconnect_delay_set(min_delay=1, max_delay=30)


def now_utc():
    return datetime.now(timezone.utc)


def init_database():
    mongo_client.admin.command("ping")
    measurements.create_index([("device_id", ASCENDING), ("received_at", DESCENDING)])
    alarm_events.create_index([("device_id", ASCENDING), ("time", DESCENDING)])
    thresholds.update_one({"_id": DEVICE_ID}, {
        "$setOnInsert": {"rules": CONFIG["alarm"]["thresholds"], "updated_at": now_utc()}
    }, upsert=True)
    LOG.info("MongoDB ready; indexes and initial thresholds available")


def reset_thresholds_from_config():
    with LOCK:
        thresholds.update_one({"_id": DEVICE_ID}, {"$set": {
            "rules": CONFIG["alarm"]["thresholds"], "updated_at": now_utc()
        }}, upsert=True)


def get_rules():
    document = thresholds.find_one({"_id": DEVICE_ID})
    return document["rules"] if document else CONFIG["alarm"]["thresholds"]


def get_latest():
    return measurements.find_one(
        {"device_id": DEVICE_ID}, sort=[("received_at", DESCENDING)]
    )


def get_history(hours=None, limit=None):
    hours = int(hours or CONFIG["dashboard"]["history_hours"])
    limit = int(limit or CONFIG["dashboard"]["max_graph_points"])
    cursor = measurements.find({
        "device_id": DEVICE_ID,
        "received_at": {"$gte": now_utc() - timedelta(hours=hours)}
    }).sort("received_at", DESCENDING).limit(limit)
    return list(reversed(list(cursor)))


def get_events(limit=15):
    return list(alarm_events.find({"device_id": DEVICE_ID})
                .sort("time", DESCENDING).limit(limit))


def validate_sensor(sensor, raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{sensor}: expected a number")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{sensor}: non-finite value")
    if sensor == "soil_moisture" and not 0 <= value <= 100:
        raise ValueError("soil moisture must be between 0 and 100%")
    if sensor == "water_ph" and not 0 <= value <= 14:
        raise ValueError("pH must be between 0 and 14")
    if sensor == "soil_ec" and value < 0:
        raise ValueError("conductivity must be non-negative")
    if sensor == "temperature" and not -60 <= value <= 150:
        raise ValueError("temperature is outside plausibility limits")
    return value


def store_measurement(payload):
    if not isinstance(payload, dict):
        raise ValueError("telemetry must be a JSON object")
    sample_id = payload.get("sample_id")
    if not isinstance(sample_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", sample_id):
        raise ValueError("sample_id must be a 1-80 character unique string")
    document = {
        "_id": f"{DEVICE_ID}:{sample_id}",
        "device_id": DEVICE_ID,
        "sample_id": sample_id,
        "received_at": now_utc(),
    }
    for field in SENSORS:
        if field not in payload:
            raise ValueError(f"missing field: {field}")
        document[field] = validate_sensor(field, payload[field])
    try:
        measurements.insert_one(document)
        return True
    except DuplicateKeyError:
        LOG.info("Duplicate sample discarded: %s", sample_id)
        return False


def evaluate(measurement, rules):
    stale = measurement is None or (
        now_utc() - measurement["received_at"]
    ).total_seconds() > int(CONFIG["alarm"]["stale_seconds"])
    states = {}
    for key in SENSORS:
        if stale:
            states[key] = "UNKNOWN"
        elif measurement[key] < rules[key]["min"]:
            states[key] = "LOW"
        elif measurement[key] > rules[key]["max"]:
            states[key] = "HIGH"
        else:
            states[key] = "NORMAL"
    return {
        "device_id": DEVICE_ID,
        "timestamp": now_utc().isoformat(),
        "last_sample_timestamp": measurement["received_at"].isoformat() if measurement else None,
        "active": None if stale else any(x != "NORMAL" for x in states.values()),
        "stale": stale,
        "states": states,
    }


def snapshot():
    with LOCK:
        measurement = get_latest()
        rules = get_rules()
        return measurement, rules, evaluate(measurement, rules)


def publish(topic, payload):
    if not client.is_connected():
        return False
    delivery = client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)
    if delivery.rc != mqtt.MQTT_ERR_SUCCESS:
        LOG.warning("MQTT queue rejected: %s", topic)
        return False
    return True


def publish_rules():
    return publish(TOPICS["thresholds"], {"device_id": DEVICE_ID, "rules": get_rules()})


def publish_alarms():
    with LOCK:
        alarm = evaluate(get_latest(), get_rules())
        previous = alarm_state.find_one({"_id": DEVICE_ID})
        if previous is None or previous.get("states") != alarm["states"]:
            alarm_events.insert_one({
                "device_id": DEVICE_ID,
                "time": now_utc(),
                "active": alarm["active"],
                "stale": alarm["stale"],
                "states": alarm["states"],
            })
            alarm_state.update_one({"_id": DEVICE_ID}, {"$set": {
                "states": alarm["states"], "time": now_utc()
            }}, upsert=True)
        return publish(TOPICS["alarms"], alarm)


def update_rules(new_rules):
    if set(new_rules) != set(SENSORS):
        raise ValueError("provide exactly four sensor rules")
    validated = {}
    for key, values in new_rules.items():
        lo = validate_sensor(key, values["min"])
        hi = validate_sensor(key, values["max"])
        if lo >= hi:
            raise ValueError(f"{key}: min must be smaller than max")
        validated[key] = {"min": lo, "max": hi}
    with LOCK:
        thresholds.update_one({"_id": DEVICE_ID}, {"$set": {
            "rules": validated, "updated_at": now_utc()
        }}, upsert=True)
        publish_rules()
        publish_alarms()


def on_connect(mqtt_client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        LOG.error("MQTT CONNECT rejected: %s", reason_code)
        return
    LOG.info("Connected to Mosquitto; subscribed to %s", TOPICS["telemetry"])
    mqtt_client.subscribe(TOPICS["telemetry"], qos=1)
    try:
        with LOCK:
            publish_rules()
            publish_alarms()
    except Exception:
        LOG.exception("Initial MQTT sync failed")


def on_message(mqtt_client, userdata, msg):
    if msg.topic != TOPICS["telemetry"] or msg.retain:
        return
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        with LOCK:
            inserted = store_measurement(payload)
            if inserted:
                LOG.info("Sample recorded: %s", payload["sample_id"])
            publish_alarms()
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        LOG.warning("Invalid MQTT telemetry: %s", exc)
    except Exception:
        LOG.exception("Error processing MQTT telemetry")


def periodic_alarm_monitor():
    while True:
        try:
            publish_alarms()
        except Exception:
            LOG.exception("Periodic alarm evaluation failed")
        time.sleep(int(CONFIG["alarm"]["check_interval_seconds"]))


def start_services():
    init_database()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(os.getenv("MQTT_HOST", "broker"), int(os.getenv("MQTT_PORT", "1883")), 60)
    client.loop_start()
    threading.Thread(target=periodic_alarm_monitor, daemon=True, name="alarms").start()
