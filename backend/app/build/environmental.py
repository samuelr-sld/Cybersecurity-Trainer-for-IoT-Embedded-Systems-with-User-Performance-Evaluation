"""The Environmental Monitoring Build Mode project — reference scenario.

This is the Build Mode counterpart to `app/scenarios/environmental.py`: the
same conceptual target (an ESP32 + BME280 environmental sensor with an OLED
display, reporting telemetry over MQTT) and, deliberately, the same broker
identity and telemetry topic the student already recovered in Hack Mode —
`app/scenarios/state.py`'s `TargetInfo` defaults. The vulnerability is the
same data-integrity issue too: the device's MQTT callback currently trusts
whatever temperature reading arrives on the telemetry topic.

Unlike the Hack Mode scenario, nothing here is executable or simulated
behaviour — this module only defines *firmware text* and where its one
student-editable region sits. `app/build/workspace.py` is what enforces that
only that region can change; this module just describes the starting state a
fresh Build Mode session loads.

PHASE 3B: this firmware is real, compilable Arduino C++ — `app/build/
compiler.py` builds it with the actual `arduino-cli` and the `esp32:esp32`
core. That is why `wifi_secrets.h` exists as its own locked file (a real
sketch cannot `#include` a header nothing provides) and why `locked_pre`
defines `readTemperatureField` (a real sketch cannot call a function nothing
defines) — both were silent gaps in Phase 3A, invisible while nothing ever
actually compiled this text, and both were caught by compiling it for real
against the installed ESP32 core (`esp32:esp32:esp32`, PubSubClient, Adafruit
BME280/SSD1306 + their dependencies). The default `security_logic` body
(`telemetryAccepted = true;`) also compiles as-is — a student who changes
nothing gets a real BUILD SUCCESS, exactly as reflective of the unremediated
vulnerability as a real compiler can make it.
"""

from __future__ import annotations

from app.build.models import BoardInfo, BuildProject, FileSegment, FirmwareFile, RegionKind

#: The stable region a future Blockly workspace will target. Must match the
#: `region_id` of the EDITABLE segment in `main.ino` below.
SECURITY_REGION_ID = "security_logic"

_MAIN_INO_LOCKED_PRE = '''/* Environmental Monitoring firmware — ESP32 + BME280 + OLED
 * Reports temperature/humidity/pressure telemetry over MQTT.
 *
 * SECURITY NOTE: incoming values on the telemetry topic are currently
 * trusted without question. The security region below is what decides
 * whether an incoming reading is acceptable before it is ever applied.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <Adafruit_BME280.h>
#include <Adafruit_SSD1306.h>
#include "mqtt_config.h"
#include "wifi_secrets.h"

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
Adafruit_BME280 bme;
Adafruit_SSD1306 display(128, 64, &Wire, -1);

float reportedTemperature = 28.0;
float reportedHumidity = 65.0;
float reportedPressure = 1008.0;

void publishTelemetry() {
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\\"temperature\\":%.1f,\\"humidity\\":%.1f,\\"pressure\\":%.1f}",
           reportedTemperature, reportedHumidity, reportedPressure);
  mqttClient.publish(MQTT_TELEMETRY_TOPIC, payload);
}

void applyTemperature(float temperature) {
  reportedTemperature = temperature;
  display.setCursor(0, 0);
  display.print("T: ");
  display.print(reportedTemperature);
}

float readTemperatureField(const String& message) {
  // Accepts either `{"temperature": 150}` or `temperature=150` — the same
  // two payload shapes Hack Mode's spoofed publish accepts (see
  // app/scenarios/payloads.py) — by finding whichever of ':' or '=' comes
  // first after the field name and parsing the rest as a float.
  int keyIndex = message.indexOf("temperature");
  if (keyIndex < 0) {
    return NAN;
  }
  int colon = message.indexOf(':', keyIndex);
  int equals = message.indexOf('=', keyIndex);
  int separator;
  if (colon >= 0 && equals >= 0) {
    separator = min(colon, equals);
  } else {
    separator = max(colon, equals);
  }
  if (separator < 0) {
    return NAN;
  }
  String rest = message.substring(separator + 1);
  rest.trim();
  return rest.toFloat();
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // ------------------------------------------------------------------
  // LOCKED SYSTEM CODE — copy the raw payload into a string the security
  // region below can inspect. Nothing here decides whether the reading is
  // trustworthy; it only makes the bytes on the wire readable.
  // ------------------------------------------------------------------
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  float incomingTemperature = readTemperatureField(message);
  bool telemetryAccepted = false;

  // ==================================================================
  // >>> BUILD_REGION BEGIN: security_logic
  // ==================================================================
  // STUDENT SECURITY REGION
  //
  // `incomingTemperature` was just parsed from whatever arrived on
  // MQTT_TELEMETRY_TOPIC — an attacker-controlled channel, as you just
  // demonstrated in Hack Mode. Decide whether this reading is acceptable
  // before it is applied to the reported environment state below.
  // Set `telemetryAccepted = true` only when it should be trusted.
'''

_MAIN_INO_EDITABLE_DEFAULT = """
  telemetryAccepted = true; // TODO: this currently trusts every reading.
"""

_MAIN_INO_LOCKED_POST = """  // ==================================================================
  // >>> BUILD_REGION END: security_logic
  // ==================================================================

  // ------------------------------------------------------------------
  // LOCKED SYSTEM CODE — apply the accepted result to system state.
  // ------------------------------------------------------------------
  if (telemetryAccepted) {
    applyTemperature(incomingTemperature);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
  }

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.connect("env-monitor-01");
  mqttClient.subscribe(MQTT_TELEMETRY_TOPIC);

  bme.begin();
  display.begin();
}

void loop() {
  mqttClient.loop();

  reportedHumidity = bme.readHumidity();
  reportedPressure = bme.readPressure() / 100.0F;
  publishTelemetry();

  delay(2000);
}
"""

_MQTT_CONFIG_H = '''/* MQTT broker configuration — Environmental Monitoring firmware.
 * LOCKED SYSTEM CODE: not part of the student security region.
 */
#ifndef MQTT_CONFIG_H
#define MQTT_CONFIG_H

#define MQTT_BROKER "192.168.10.10"
#define MQTT_PORT 1883
#define MQTT_TELEMETRY_TOPIC "sensors/bme280/telemetry"

#endif
'''

_WIFI_SECRETS_H = '''/* Wi-Fi credentials — Environmental Monitoring firmware.
 * LOCKED SYSTEM CODE: not part of the student security region. A real
 * project keeps this file out of version control; the sandbox's values
 * are fake and only exist so the sketch has something to #include.
 */
#ifndef WIFI_SECRETS_H
#define WIFI_SECRETS_H

#define WIFI_SSID "SANDBOX-LAB"
#define WIFI_PASS "isolated-only"

#endif
'''


def create_environmental_monitoring_project() -> BuildProject:
    """Build a fresh Environmental Monitoring project.

    One call, one independent `BuildProject` — this is what a fresh Build
    Mode session wraps in its own `BuildWorkspace`, so no two sessions can
    ever share (or corrupt) each other's firmware state.
    """
    main_ino = FirmwareFile(
        path="main.ino",
        segments=(
            FileSegment(RegionKind.LOCKED, "locked_pre", _MAIN_INO_LOCKED_PRE),
            FileSegment(RegionKind.EDITABLE, SECURITY_REGION_ID, _MAIN_INO_EDITABLE_DEFAULT),
            FileSegment(RegionKind.LOCKED, "locked_post", _MAIN_INO_LOCKED_POST),
        ),
    )
    mqtt_config_h = FirmwareFile(
        path="mqtt_config.h",
        segments=(FileSegment(RegionKind.LOCKED, "locked_mqtt_config", _MQTT_CONFIG_H),),
    )
    wifi_secrets_h = FirmwareFile(
        path="wifi_secrets.h",
        segments=(FileSegment(RegionKind.LOCKED, "locked_wifi_secrets", _WIFI_SECRETS_H),),
    )
    return BuildProject(
        project_id="environmental-monitoring-default",
        scenario_id="environmental-monitoring",
        module_id="mqtt_telemetry_trust",
        firmware_name="Environmental Monitor Firmware",
        board=BoardInfo(name="ESP32 Dev Module", mcu="ESP32", fqbn="esp32:esp32:esp32"),
        files=(main_ino, mqtt_config_h, wifi_secrets_h),
        security_region_id=SECURITY_REGION_ID,
    )
