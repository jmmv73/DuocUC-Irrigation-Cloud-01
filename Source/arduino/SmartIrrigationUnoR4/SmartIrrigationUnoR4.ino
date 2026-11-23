/*
 * UNO R4 WiFi -> DigitalOcean Mosquitto (plain MQTT TCP port 1883).
 * Board: Arduino UNO R4 WiFi; libraries: WiFiS3, ArduinoMqttClient, ArduinoJson 7.
 * DEMO sensor generator only; replace readSensors() with calibrated sensor code.
 * Uses Droplet public IPv4 address. No certificate handling.
 * Intended for short classroom demonstrations, not production security.
 */
#include <WiFiS3.h>
#include <ArduinoMqttClient.h>
#include <ArduinoJson.h>
#include "secrets.h"
#include "connection.h"  // Generated from config/settings.json by configure.py

WiFiClient wifiClient;
MqttClient mqtt(wifiClient);

const char *TOPIC_TELEMETRY = MQTT_TOPIC_TELEMETRY;
const char *TOPIC_ALARMS = MQTT_TOPIC_ALARMS;
const char *TOPIC_THRESHOLDS = MQTT_TOPIC_THRESHOLDS;
const unsigned long SAMPLE_INTERVAL_MS = 30000;
const unsigned long ALARM_TIMEOUT_MS = 45000;

unsigned long lastSampleMs = 0;
unsigned long lastAlarmMs = 0;
unsigned long lastReconnectAttemptMs = 0;
unsigned long sequenceNumber = 0;
String bootId;
bool alarmActive = false;
bool alarmValid = false;

void handleMessage(int messageSize) {
  String topic = mqtt.messageTopic();
  String payload;
  payload.reserve(messageSize + 1);
  while (mqtt.available()) {
    payload += (char)mqtt.read();
  }
  JsonDocument obj;
  DeserializationError err = deserializeJson(obj, payload);
  if (err) {
    Serial.println("Invalid JSON on incoming MQTT topic");
    return;
  }
  if (topic == TOPIC_ALARMS) {
    lastAlarmMs = millis();
    if (obj["stale"].is<bool>() && !obj["stale"].as<bool>() && obj["active"].is<bool>()) {
      alarmActive = obj["active"].as<bool>();
      alarmValid = true;
    } else {
      alarmValid = false;
    }
    Serial.print("Alarm: ");
    Serial.println(payload);
  } else if (topic == TOPIC_THRESHOLDS) {
    Serial.print("Thresholds: ");
    Serial.println(payload);
  }
}

// Illustrative measurements. Replace all four values with actual calibrated sensors.
void readSensors(float &moisture, float &tempC, float &ec, float &ph) {
  float x = (float)((millis() / 1000UL) % 80);
  moisture = 40.0 + (x / 8.0);
  tempC = 24.0 + (x / 100.0);
  ec = 1.25;
  ph = 6.7;
}

void publishTelemetry() {
  float moisture, temperature, ec, ph;
  readSensors(moisture, temperature, ec, ph);
  JsonDocument doc;
  doc["sample_id"] = bootId + "-" + String(++sequenceNumber);
  doc["soil_moisture"] = moisture;
  doc["temperature"] = temperature;
  doc["soil_ec"] = ec;
  doc["water_ph"] = ph;
  String json;
  serializeJson(doc, json);
  mqtt.beginMessage(TOPIC_TELEMETRY, (unsigned long)json.length(), false, 1);
  mqtt.print(json);
  mqtt.endMessage();
  Serial.print("Telemetry: ");
  Serial.println(json);
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Connecting to WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? "connected" : "failed");
}

void connectMqtt() {
  if (mqtt.connected()) return;
  if (millis() - lastReconnectAttemptMs < 5000) return;
  lastReconnectAttemptMs = millis();
  mqtt.setId("arduino-uno01");
  Serial.print("Connecting to MQTT...");
  if (mqtt.connect(MQTT_HOST, MQTT_PORT)) {
    mqtt.subscribe(TOPIC_ALARMS, 1);
    mqtt.subscribe(TOPIC_THRESHOLDS, 1);
    alarmValid = false;
    Serial.println("connected");
  } else {
    Serial.print("failed, code=");
    Serial.println(mqtt.connectError());
  }
}

void updateLed() {
  bool offline = !mqtt.connected() || !alarmValid || millis() - lastAlarmMs > ALARM_TIMEOUT_MS;
  if (offline) {
    digitalWrite(LED_BUILTIN, (millis() / 350) % 2);  // blinking = unknown/offline
  } else {
    digitalWrite(LED_BUILTIN, alarmActive ? HIGH : LOW); // ON = alarm
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  randomSeed(micros());
  bootId = String((unsigned long)random(1000000, 2000000000), HEX);
  mqtt.onMessage(handleMessage);
  connectWifi();
  lastReconnectAttemptMs = millis() - 5000;
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (WiFi.status() == WL_CONNECTED) connectMqtt();
  if (mqtt.connected()) {
    mqtt.poll();
    if (millis() - lastSampleMs >= SAMPLE_INTERVAL_MS || lastSampleMs == 0) {
      lastSampleMs = millis();
      publishTelemetry();
    }
  }
  updateLed();
  delay(15);
}
