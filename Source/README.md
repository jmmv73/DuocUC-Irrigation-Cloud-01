# Smart Irrigation IoT — Minimal DigitalOcean HTTP prototype

**Arduino UNO R4 WiFi ⇄ MQTT/Mosquitto ⇄ Python Dash ⇄ MongoDB**, on **one Ubuntu DigitalOcean Droplet**.

This edition deliberately **does not use HTTPS, TLS, certificates, public DNS, a reverse proxy, a dashboard login, MQTT user/password, or AWS**. It is intended for a **short classroom demonstration** on a disposable Droplet, not a production service. Anyone able to reach the public MQTT and web ports can read or modify device data and alarm settings. Do not use the public Droplet for real equipment control, private data, or sustained deployment.

## 1. What the students will build

| Docker service | Purpose | Public port |
|---|---|---|
| Mosquitto 2 | Receives telemetry; delivers alarms and configuration via MQTT | TCP `1883` |
| Python 3.12 + Dash + Paho | Displays dashboard; reads/writes MongoDB; implements threshold alarms | TCP `8080` (HTTP) |
| MongoDB 8 | Stores sensor measurements, threshold settings and alarm transitions | **None** (Docker network only) |

The dashboard is opened directly in a browser at **`http://DROPLET_IP:8080`**. The Arduino and test simulator connect to **`DROPLET_IP:1883`** using ordinary MQTT. **No DNS or SSH tunnel is needed.**

All three services are separated by Docker Compose and use persistent Docker volumes for MQTT and MongoDB. The incoming telemetry message contains `soil_moisture`, `temperature`, `soil_ec`, `water_ph`, and a unique `sample_id`; Paho stores validated messages in MongoDB. The dashboard supports current readings, historical plots, editable min/max limits, alarm event history and outgoing alarm messages to Arduino.

## 2. Requirements

- One DigitalOcean Droplet, Ubuntu **24.04 LTS**, **1 vCPU / 1 GB RAM** as a starting point.
- **Arduino UNO R4 WiFi** and a working Wi-Fi network; sensors optional for the first test (sketch simulates sensor values).
- Public Droplet IPv4 address (`DROPLET_IP`). **No domain name required.**
- A computer with SSH/SCP, browser, Python 3 and Arduino IDE.

**1 GB note:** MongoDB 8 is configured with a 0.25 GB WiredTiger cache, but this is not a total RSS limit. Python and MongoDB may still exceed 1 GB under some loads, especially during image builds. Check `docker stats`; if containers are OOM-killed, use a 2 GB Droplet or build the Python image on a separate machine. MongoDB 8 also requires appropriate CPU instruction support (AVX on x86-64).

## STEP 1 — Create/access the Droplet

Create a Basic Ubuntu 24.04 Droplet in DigitalOcean and record its public IPv4 address. If using root/password login for this short lab, you can use:

```bash
ssh root@DROPLET_IP
```

Keep a strong unique root password; this setup is deliberately not hardened. In the **DigitalOcean Cloud Firewall**, allow inbound **TCP 22** (SSH), **TCP 1883** (MQTT), **TCP 8080** (dashboard). Keep **27017 closed**. Allow outbound connections (Docker image downloads and apt). Do not open ports 80, 443 or 8883 for this version.

## STEP 2 — Install Docker and Python on the Droplet

As root on Ubuntu 24.04:

```bash
apt update
apt install -y docker.io docker-compose-v2 python3 python3-venv unzip
systemctl enable --now docker
docker --version
docker compose version
```

If the `docker-compose-v2` package is not available, enable the Ubuntu `universe` repository or install Docker/Compose from Docker's official package repository: https://docs.docker.com/engine/install/ubuntu/

This guide uses Ubuntu's packaged Docker Engine and Compose V2 for the simplest prototype setup. Avoid mixing Ubuntu and Docker Inc. package sources on one machine.

## STEP 3 — Upload and extract the ZIP

From your laptop/computer, upload the included project ZIP:

```bash
scp Smart_Irrigation_DO_HTTP.zip root@DROPLET_IP:/opt/
```

On the Droplet:

```bash
cd /opt
unzip Smart_Irrigation_DO_HTTP.zip
cd Smart_Irrigation_DO_HTTP
```

All subsequent commands are run from this directory unless otherwise stated.

## STEP 4 — Configure exactly one settings file

```bash
nano config/settings.json
```

**Mandatory change:** replace `"public_host": "YOUR_DROPLET_IP"` with the Droplet's real IP, for example `"public_host": "203.0.113.10"` (**203.0.113.10 is only a documentation example**, not a real working host).

Other options in `config/settings.json`:

| JSON setting | Description / default |
|---|---|
| `public_host` | Public IP or hostname, used by the simulator and generated Arduino header |
| `device_id` | Device identifier (`uno01`) |
| `dashboard.title` | Title displayed in the browser |
| `dashboard.port` | Public HTTP port: `8080` |
| `dashboard.refresh_ms` | Refresh every 5000 ms |
| `dashboard.history_hours` | Default history window: 24 h |
| `dashboard.max_graph_points` | Max points returned per graph request: 2500 |
| `mqtt.port` | Public MQTT TCP port: `1883` |
| `mqtt.topics.telemetry` | Sensor readings, Arduino → Python |
| `mqtt.topics.alarms` | Alarm results, Python → Arduino |
| `mqtt.topics.thresholds` | Current thresholds, Python → Arduino |
| `alarm.stale_seconds` | Mark device UNKNOWN if last reading is older than 90 sec |
| `alarm.check_interval_seconds` | Re-evaluate state every 10 sec |
| `alarm.thresholds` | Initial min/max values for four sensors |
| `simulator.mode` | Default test scenario (`cycle`, `normal`, `dry`, `high-ph`, `hot`, `high-ec`, `mixed`) |
| `simulator.samples` | Default number of samples; 0 means continuous simulation |
| `simulator.interval_seconds` | Default seconds between simulated samples |

The `sample_id` must be unique, including across Arduino resets. The example Arduino sketch generates a boot identifier and sequence counter. MQTT QoS 1 can redeliver samples; the backend deduplicates by this ID.

The initial thresholds are **illustrative only**, not crop recommendations. Moisture depends on the probe's calibration, EC on its measurement circuit and conditions, and pH needs probe calibration.

## STEP 5 — Start the project

Run the configuration/deployment script:

```bash
python3 scripts/configure.py init
```

The script performs the following:

1. Validates your JSON config (ports, topic names, sensor limits).
2. Generates `.env` with published ports.
3. Generates Mosquitto's plain MQTT listener configuration (anonymous, no TLS).
4. Generates `arduino/SmartIrrigationUnoR4/connection.h` with the host, port and topics from the JSON file.
5. Checks and starts Docker Compose, building the Python dashboard image.
6. Prints the dashboard and MQTT addresses.

Verify:

```bash
docker compose ps
docker compose logs --tail=70 broker
docker compose logs --tail=70 mongo
docker compose logs --tail=70 dashboard
docker stats --no-stream
```

The first build and MongoDB initialization consume more memory than steady-state operation. If the dashboard is restarting, inspect `docker compose logs dashboard`. If MongoDB cannot become healthy, inspect `docker compose logs mongo` and your Droplet's RAM/CPU compatibility.

## STEP 6 — Open the dashboard in a regular browser

On your laptop, tablet or mobile browser, open:

```
http://DROPLET_IP:8080
```

There is **no login screen**. The dashboard initially reports **MQTT ONLINE** and **NO RECENT DATA** until the first measurement arrives. The browser refreshes automatically.

Check the URL includes the explicit `http://` scheme and port `:8080`; browsers sometimes default to HTTPS when a protocol is not provided.

## STEP 7 — Send test data before connecting Arduino

On the Droplet, the MQTT command-line utility provides a quick smoke test:

```bash
apt install -y mosquitto-clients
mosquitto_pub -h 127.0.0.1 -p 1883 -t riego/uno01/telemetry -m '{"sample_id":"manual-001","soil_moisture":20,"temperature":25.0,"soil_ec":1.4,"water_ph":8.3}'
```

Now refresh the dashboard: you should see the measurement and alarm states (`LOW` for moisture, `HIGH` for pH with the initial thresholds). Watch returned alarm messages:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'riego/uno01/alarms' -v
```

### Recommended: interactive demonstration without Arduino

The included **Python simulator** emulates the four sensor measurements and the MQTT feedback path. Run it on your laptop or on the Droplet; it connects to the actual Mosquitto broker, writes through the real backend into MongoDB, and listens for returning alarm and threshold messages.

Install the only additional simulator dependency (on your laptop or Droplet):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install paho-mqtt==2.1.0
```

On Windows PowerShell, activation is `.venv\Scripts\Activate.ps1`.

**Full class demonstration** (normal → dry soil → high pH → combined alarms → recovery):

```bash
python tests/simulator.py --host DROPLET_IP --mode cycle --samples 15 --interval 3
```

Keep the dashboard open at `http://DROPLET_IP:8080` while the simulator runs. The graph should grow; the displayed alarm states and returned MQTT messages should change over time. `--host` is optional if `config/settings.json` has the correct public IP.

**Other useful runs:**

```bash
# All four sensors inside their configured threshold ranges
python tests/simulator.py --mode normal --samples 10 --interval 3

# Simulate only dry soil (LOW soil moisture)
python tests/simulator.py --mode dry --samples 8 --interval 3

# Simulate only excessive water pH (HIGH pH)
python tests/simulator.py --mode high-ph --samples 8 --interval 3

# Simulate both faults together (same as --fault from previous prototype)
python tests/simulator.py --fault --samples 8 --interval 3

# Run continuously, once every 30 seconds, like the Arduino
python tests/simulator.py --mode normal --samples 0 --interval 30
```

The additional modes `hot` and `high-ec` trigger high-temperature and high-conductivity alarms respectively. `--seed 42` makes randomized normal-value jitter reproducible. MQTT messages use QoS 1 and unique `sample_id` identifiers; the simulator subscribes to both alarm and threshold topics and prints received results.

The default simulator settings are also in the same **`config/settings.json`** used by the rest of the project:

```json
"simulator": {
  "mode": "cycle",
  "interval_seconds": 5,
  "samples": 15
}
```

A command-line option overrides the configured default. The simulator works without Arduino hardware, but it **does require a running reachable broker and dashboard backend** to verify the complete loop. For a no-network test of sample-generation rules, run `python3 -m unittest discover -s tests -v`.

**Important:** simulated data is a teaching tool; it does not reproduce noise, calibration limitations or measurement physics of the real probes. Changing the dashboard's live thresholds does not automatically change this simulator's configuration file; update the JSON or reset dashboard thresholds if you want perfectly aligned nominal and fault conditions.

## STEP 8 — Connect Arduino UNO R4 WiFi

On your computer:

1. Install Arduino IDE, select board **Arduino UNO R4 WiFi**, and install libraries **ArduinoMqttClient** and **ArduinoJson (v7)**. `WiFiS3` is the board's Wi-Fi library.
2. Copy `arduino/SmartIrrigationUnoR4/secrets.example.h` to `arduino/SmartIrrigationUnoR4/secrets.h`, then set **your Wi-Fi SSID and password**.
3. Copy the **generated** `arduino/SmartIrrigationUnoR4/connection.h` from the Droplet to the same Arduino sketch folder on your computer. If you have edited `config/settings.json` locally, you can generate it locally **without Docker** using `python3 scripts/configure.py render`.
4. Open `SmartIrrigationUnoR4.ino` in the IDE. The sketch uses **`WiFiClient` and plain MQTT port 1883**—no TLS certificate imports, MQTT usernames or MQTT passwords.
5. Upload the sketch and open Serial Monitor at **115200 baud**.

The demo sketch transmits simulated sensor data every **30 seconds**, subscribes to alarms and thresholds, and indicates alarm state via the onboard LED:

- LED continuously on: at least one abnormal measurement.
- LED off: all monitored readings normal.
- LED blinking: no MQTT connection, invalid alarm message, or alarm message timeout.

Replace `readSensors()` with your calibrated analog/digital sensor interfaces when the four physical sensors are available.

### Arduino file synchronization

The Python update script writes `connection.h` from the central JSON configuration. It does **not** remotely modify the flashed Arduino program. If MQTT host/port/topics change, copy the regenerated `connection.h` to the laptop and **recompile and upload** the sketch.

## STEP 9 — Modify topic names or dashboard settings

Edit just this file:

```bash
nano config/settings.json
```

Apply changes:

```bash
./scripts/update_config.sh
```

This validates, regenerates supporting files, runs `docker compose up -d --build`, and restarts the broker/dashboard when necessary. **There are no certificates to renew.**

For example, change:

```json
"telemetry": "lab/group03/measurements",
"alarms": "lab/group03/alarms",
"thresholds": "lab/group03/configuration"
```

Then execute `./scripts/update_config.sh`, copy the regenerated Arduino header and reflash the board. The backend automatically subscribes to the new telemetry topic after restart.

**Threshold note:** the JSON thresholds **seed MongoDB only on the first initialization**. Limits changed in the dashboard are stored permanently. If you want to overwrite the database's thresholds with those in the JSON file, run:

```bash
./scripts/update_config.sh --reset-thresholds
```

This also republishes the updated thresholds and alarm state over MQTT.

If you only need the Arduino header or want to validate before applying:

```bash
python3 scripts/configure.py validate
python3 scripts/configure.py render
```

`render` needs Python 3 only; it does not require Docker. The generated Arduino header can therefore be prepared on macOS/Windows/Linux as well.

## STEP 10 — Useful operational commands

```bash
docker compose ps                             # status
docker compose logs -f dashboard              # live dashboard logs
docker compose logs -f broker                 # live MQTT logs
docker compose logs -f mongo                  # database errors
docker stats --no-stream                      # container CPU/RAM
docker compose restart dashboard             # restart only Python
docker compose down                          # stop/remove containers, preserve data
docker compose up -d                          # start again, preserve data
```

**Do not run `docker compose down -v`** unless you explicitly intend to delete the MongoDB and broker volumes. `docker compose down` alone keeps named volumes.

### Basic MongoDB inspection

```bash
docker compose exec mongo mongosh --quiet --eval \
  'db.getSiblingDB("riego").measurements.countDocuments({})'
```

### Troubleshooting

| Symptom | Check |
|---|---|
| Cannot open `http://IP:8080` | Cloud firewall TCP 8080, `docker compose ps`, dashboard logs, check actual IP |
| MQTT timeout | Cloud firewall TCP 1883, Mosquitto logs, Wi-Fi network restrictions |
| Dashboard shows MQTT OFFLINE | Broker logs and Python logs; Docker network DNS name `broker` |
| No points in graph | Simulator payload keys, telemetry topic matches JSON, Python logs |
| New JSON thresholds not reflected | Run update with `--reset-thresholds` (default preserves dashboard settings) |
| Arduino cannot connect | `connection.h` host/port, router restrictions, `WiFiS3` status |
| MongoDB restarts | `docker stats`, `free -h`, storage, CPU AVX support |
| Build fails on 1 GB RAM | Try 2 GB temporarily or prebuild/pull the Python image |

## 11. What each source file means

| File | What students should understand |
|---|---|
| `compose.yaml` | Services, container names, port mapping, private network, volume persistence |
| `config/settings.json` | Single source of application/device configuration |
| `scripts/configure.py` | Validation, settings generation and Docker Compose deployment |
| `scripts/update_config.sh` | One-command configuration update |
| `generated/mosquitto/mosquitto.conf` | Mosquitto TCP listener, persistence and anonymous access for demo |
| `app/Dockerfile` | Python image build, dependencies and non-root application user |
| `app/requirements.txt` | Python packages: Dash, Plotly, Paho MQTT, PyMongo, Waitress |
| `app/core.py` | MQTT callbacks, telemetry validation, MongoDB CRUD, alarm state machine and MQTT publishing |
| `app/app.py` | Dash layout, historical charts, live refresh, editable thresholds and alarm history |
| `tests/simulator.py` | MQTT client: configurable scenarios, publishes telemetry and displays returned alarms |
| `tests/sim_data.py` | Synthetic sensor readings derived from configured min/max thresholds |
| `tests/test_simulator.py` | Offline tests for each simulator scenario and threshold crossing |
| `tests/test_config.py` | Input-validation and generated-configuration unit tests |
| `arduino/SmartIrrigationUnoR4/SmartIrrigationUnoR4.ino` | Wi-Fi, MQTT, JSON telemetry, subscribed alarm messages and LED |
| `arduino/SmartIrrigationUnoR4/connection.h` | Auto-generated host/port/topic names; required at compile time |
| `arduino/SmartIrrigationUnoR4/secrets.h` | Student's Wi-Fi credentials; excluded from Git |

## 12. Functional validation checklist

- [ ] All three containers are `Up` and MongoDB is healthy.
- [ ] Dashboard loads at `http://DROPLET_IP:8080` from another device.
- [ ] A test MQTT message appears in history.
- [ ] `--mode cycle` produces normal → dry → high-pH → combined alarms → recovery and returning MQTT alarm messages.
- [ ] Out-of-range humidity and pH generate the expected LOW/HIGH alarm states.
- [ ] Changing dashboard thresholds changes the outgoing alarm status.
- [ ] Arduino receives alarm messages and changes LED behavior.
- [ ] Restarting Docker preserves measurements and thresholds.
- [ ] When telemetry stops, the state eventually changes to `UNKNOWN`.
- [ ] A changed topic name in JSON is reflected in Python and the generated Arduino header.

## 13. Limitations and scope

This is a **demonstration**, not a secured or certified control system. There is no HTTP authentication, no MQTT access control, and no encryption between clients and the public Droplet. A third party who can reach the ports could publish fake sensor readings or modify thresholds. Restrict firewall source IPs where practical, use dummy data, do not connect irrigation pumps or other actuators, and destroy the test Droplet after the lab if it is not needed.

The code retains original simple alarm logic; it does not implement hysteresis or complex agronomic recommendations. The backend is designed for one Arduino and one Python application process. A 1 GB Droplet may be borderline for MongoDB plus image building. The software has not been end-to-end validated on the user's actual Droplet or physical Arduino: perform the checklist before handing it to student groups.
