"""Offline tests of the simulated sensor and alarm scenarios (no MQTT required)."""
import copy
import importlib.util
import json
import random
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sim_data", BASE / "tests/sim_data.py")
sim_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sim_data)


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((BASE / "config/settings.json").read_text())
        self.rules = self.config["alarm"]["thresholds"]

    def generate(self, mode, index=0):
        return sim_data.make_sample(self.config, mode, index=index, rng=random.Random(123), prefix="test")

    def test_normal_values_inside_limits(self):
        for i in range(12):
            mode, data = self.generate("normal", i)
            self.assertEqual(mode, "normal")
            for key, rule in self.rules.items():
                self.assertGreaterEqual(data[key], rule["min"])
                self.assertLessEqual(data[key], rule["max"])
            self.assertEqual(data["sample_id"], f"test-{i:06d}")

    def test_dry_triggers_moisture_low(self):
        _, data = self.generate("dry")
        self.assertLess(data["soil_moisture"], self.rules["soil_moisture"]["min"])

    def test_high_ph_triggers_ph_high(self):
        _, data = self.generate("high-ph")
        self.assertGreater(data["water_ph"], self.rules["water_ph"]["max"])

    def test_mixed_triggers_two_alarms(self):
        _, data = self.generate("mixed")
        self.assertLess(data["soil_moisture"], self.rules["soil_moisture"]["min"])
        self.assertGreater(data["water_ph"], self.rules["water_ph"]["max"])

    def test_hot_and_ec_high(self):
        _, hot = self.generate("hot")
        _, high_ec = self.generate("high-ec")
        self.assertGreater(hot["temperature"], self.rules["temperature"]["max"])
        self.assertGreater(high_ec["soil_ec"], self.rules["soil_ec"]["max"])

    def test_cycle(self):
        expected = ["normal"]*3 + ["dry"]*3 + ["high-ph"]*3 + ["mixed"]*3 + ["normal"]*3
        self.assertEqual([sim_data.stage("cycle", i) for i in range(15)], expected)

    def test_configurable_thresholds(self):
        custom = copy.deepcopy(self.config)
        custom["alarm"]["thresholds"]["soil_moisture"] = {"min": 45, "max": 75}
        _, values = sim_data.make_sample(custom, "dry", rng=random.Random(100), prefix="test")
        self.assertLess(values["soil_moisture"], 45)

    def test_impossible_faults_are_clear(self):
        custom = copy.deepcopy(self.config)
        custom["alarm"]["thresholds"]["soil_moisture"]["min"] = 0
        with self.assertRaisesRegex(ValueError, "Cannot simulate dry-soil"):
            sim_data.make_sample(custom, "dry")
        custom = copy.deepcopy(self.config)
        custom["alarm"]["thresholds"]["water_ph"]["max"] = 14
        with self.assertRaisesRegex(ValueError, "Cannot simulate high-pH"):
            sim_data.make_sample(custom, "high-ph")


if __name__ == "__main__":
    unittest.main()
