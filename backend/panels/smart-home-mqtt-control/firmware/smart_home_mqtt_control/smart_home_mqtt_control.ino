/*
 * Smart Home MQTT Control System -- Panel 1 firmware resource (Phase 2D.2).
 *
 * This is a RESOURCE placeholder that integrates the Panel 1 firmware into
 * the panel package architecture. It sketches a Smart Home MQTT control
 * node (an ESP32 that subscribes to command topics and drives a relay) so
 * the package's FirmwareConfiguration resolves to a real, compilable .ino
 * directory. It is deliberately NOT the finalized vulnerable firmware and
 * NOT the Panel 1 Hack activity: the weak-authentication vulnerability, its
 * attack/remediation lifecycle, and the secured revision belong to a later
 * phase (see backend/app/panels/__init__.py for the scope boundary).
 *
 * Nothing in the trainer compiles or flashes this as a side effect of
 * loading the package. Provisioning is a later phase; package loading only
 * verifies that this resource exists.
 */

#include <WiFi.h>
#include <PubSubClient.h>

// --- placeholder configuration ---------------------------------------------
// Real credentials/broker details are provisioning inputs for a later phase,
// not committed here. These constants only let the sketch compile.
static const char *WIFI_SSID = "SMART_HOME_LAB";
static const char *WIFI_PASSWORD = "changeme";
static const char *MQTT_BROKER = "192.168.10.20";
static const uint16_t MQTT_PORT = 1883;
static const char *COMMAND_TOPIC = "home/livingroom/light/set";
static const char *STATE_TOPIC = "home/livingroom/light/state";

static const uint8_t RELAY_PIN = 26;

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

static void applyCommand(const String &payload) {
  const bool on = payload.equalsIgnoreCase("ON");
  digitalWrite(RELAY_PIN, on ? HIGH : LOW);
  mqtt.publish(STATE_TOPIC, on ? "ON" : "OFF", true);
}

static void onMessage(char *topic, byte *payload, unsigned int length) {
  String message;
  message.reserve(length);
  for (unsigned int i = 0; i < length; i++) {
    message += static_cast<char>(payload[i]);
  }
  if (String(topic) == COMMAND_TOPIC) {
    applyCommand(message);
  }
}

static void ensureConnected() {
  while (!mqtt.connected()) {
    if (mqtt.connect("smart-home-mqtt-control")) {
      mqtt.subscribe(COMMAND_TOPIC);
    } else {
      delay(1000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMessage);
}

void loop() {
  ensureConnected();
  mqtt.loop();
}
