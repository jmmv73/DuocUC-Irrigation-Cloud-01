"""Pure sensor-data generator for the classroom MQTT simulator.

Separated from MQTT networking so students can test alarm scenarios offline.
Values are synthetic; they are NOT calibrated sensor measurements.
"""
import random
import uuid

MODES = ("normal", "dry", "high-ph", "hot", "high-ec", "mixed", "cycle")
CYCLE_STAGES = ("normal", "dry", "high-ph", "mixed", "normal")
FIELDS = ("soil_moisture", "temperature", "soil_ec", "water_ph")


def stage(mode, index):
    """Return an effective scenario; cycle spends three samples in each stage."""
    if mode not in MODES:
        raise ValueError(f"Unknown simulator mode: {mode}")
    if mode == "cycle":
        return CYCLE_STAGES[(index // 3) % len(CYCLE_STAGES)]
    return mode


def make_sample(config, mode="normal", index=0, rng=None, prefix=None):
    """Generate one valid telemetry message and its expected alarm scenario."""
    rng = rng or random.Random()
    rules = config["alarm"]["thresholds"]
    selected = stage(mode, index)

    # Jitter stays within the normal operating band, irrespective of the
    # teacher's configurable min/max thresholds.
    values = {}
    for key in FIELDS:
        lo = float(rules[key]["min"])
        hi = float(rules[key]["max"])
        values[key] = (lo + hi) / 2 + rng.uniform(-0.10, 0.10) * (hi - lo)

    if selected in ("dry", "mixed"):
        lower = float(rules["soil_moisture"]["min"])
        if lower <= 0:
            raise ValueError("Cannot simulate dry-soil LOW: moisture minimum is already 0%")
        values["soil_moisture"] = max(0.0, lower - min(5.0, lower / 2))

    if selected in ("high-ph", "mixed"):
        upper = float(rules["water_ph"]["max"])
        if upper >= 14:
            raise ValueError("Cannot simulate high-pH HIGH: pH maximum is already 14")
        values["water_ph"] = min(14.0, upper + min(0.8, (14 - upper) / 2))

    if selected == "hot":
        upper = float(rules["temperature"]["max"])
        if upper >= 150:
            raise ValueError("Cannot simulate temperature HIGH at the sensor plausibility limit")
        values["temperature"] = min(150.0, upper + min(3.0, (150 - upper) / 2))

    if selected == "high-ec":
        upper = float(rules["soil_ec"]["max"])
        values["soil_ec"] = upper + 0.5

    message = {
        "sample_id": f"{prefix or uuid.uuid4().hex[:12]}-{index:06d}",
        **{key: round(value, 2) for key, value in values.items()},
    }
    return selected, message
