"""Unit tests run without a Docker daemon or a DigitalOcean Droplet."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("configure", BASE / "scripts/configure.py")
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((BASE / "config/settings.json").read_text())
        self.cfg["public_host"] = "203.0.113.10"  # Documentation TEST-NET IP, not a real server

    def test_good_config(self):
        self.assertEqual(configure.validate(self.cfg), self.cfg)

    def test_no_domain_or_email_required(self):
        self.assertNotIn("domain", self.cfg)
        self.assertNotIn("acme_email", self.cfg)
        configure.validate(self.cfg)

    def test_default_placeholder_invalid(self):
        self.cfg["public_host"] = "YOUR_DROPLET_IP"
        with self.assertRaises(ValueError):
            configure.validate(self.cfg)

    def test_topics_no_wildcards(self):
        self.cfg["mqtt"]["topics"]["telemetry"] = "riego/#"
        with self.assertRaises(ValueError):
            configure.validate(self.cfg)

    def test_duplicate_topics_invalid(self):
        self.cfg["mqtt"]["topics"]["alarms"] = self.cfg["mqtt"]["topics"]["telemetry"]
        with self.assertRaises(ValueError):
            configure.validate(self.cfg)

    def test_bad_threshold(self):
        self.cfg["alarm"]["thresholds"]["water_ph"]["min"] = 12
        with self.assertRaises(ValueError):
            configure.validate(self.cfg)

    def test_bad_port(self):
        self.cfg["mqtt"]["port"] = 8080
        with self.assertRaises(ValueError):
            configure.validate(self.cfg)

    def test_render_no_docker(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir()
            (root / "arduino/SmartIrrigationUnoR4").mkdir(parents=True)
            (root / "config/settings.json").write_text(json.dumps(self.cfg))
            with patch.object(configure, "ROOT", root), \
                 patch.object(configure, "SETTINGS", root / "config/settings.json"), \
                 patch.object(configure, "ENV", root / ".env"), \
                 patch.object(configure, "GENERATED", root / "generated"), \
                 patch.object(configure, "STATE", root / "generated/state.json"), \
                 patch.object(configure, "ARDUINO_HEADER", root / "arduino/SmartIrrigationUnoR4/connection.h"):
                first = configure.render(self.cfg)
                second = configure.render(self.cfg)
                self.assertEqual(first, (True, True))
                self.assertEqual(second, (False, False))
                cfg = (root / "generated/mosquitto/mosquitto.conf").read_text()
                header = (root / "arduino/SmartIrrigationUnoR4/connection.h").read_text()
                env = (root / ".env").read_text()
                self.assertIn("listener 1883", cfg)
                self.assertIn("allow_anonymous true", cfg)
                self.assertIn("#define MQTT_HOST \"203.0.113.10\"", header)
                self.assertIn("#define MQTT_TOPIC_ALARMS", header)
                self.assertIn("MQTT_PORT=1883", env)
                self.assertNotIn("TRAEFIK", env)

    def test_atomic_file_writer(self):
        with TemporaryDirectory() as temp:
            p = Path(temp) / "file.txt"
            self.assertTrue(configure.save_if_changed(p, "abc"))
            self.assertFalse(configure.save_if_changed(p, "abc"))
            self.assertTrue(configure.save_if_changed(p, "xyz"))
            self.assertEqual(p.read_text(), "xyz")


if __name__ == "__main__":
    unittest.main()
