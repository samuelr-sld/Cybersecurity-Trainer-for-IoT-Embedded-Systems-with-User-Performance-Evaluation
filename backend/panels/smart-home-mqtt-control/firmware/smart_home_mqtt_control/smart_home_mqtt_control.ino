/*
 * Smart Home MQTT Control System -- Panel 1 vulnerable firmware (Phase 2H.2).
 *
 * Source-aligned with this panel's package and scenario:
 *   - backend/panels/smart-home-mqtt-control/panel.json
 *   - backend/app/scenarios/smart_home.py
 *   - backend/app/scenarios/smart_home_state.py
 *
 * All agree on one activity: an ESP32 drives a small DC motor through an
 * L293D driver, with local START/STOP push-buttons, a green "running" LED, a
 * red "stopped" LED, and a buzzer that chirps on a state change. The motor
 * can be commanded two ways -- by the local buttons, or remotely over MQTT on
 * COMMAND_TOPIC -- and the device republishes its state on STATE_TOPIC.
 *
 * FINALIZED LOCAL TRAINING ARCHITECTURE (Phase 2H.2). The panel no longer
 * talks to a cloud broker. It joins the isolated Raspberry Pi training AP
 * "CyberTrainer" and connects to the Mosquitto broker the Pi hosts at
 * 192.168.50.1:1883 over plain local MQTT (no TLS -- this is a closed lab
 * network, not the public internet):
 *
 *      Raspberry Pi 5  ->  Wi-Fi AP "CyberTrainer" (192.168.50.1)
 *                          Mosquitto MQTT broker :1883
 *                              ^
 *                              |  cybertrainer/smart-home/motor/control
 *                          ESP32 Panel 1
 *
 * THE WEAKNESS IS MISSING AUTHORIZATION FOR CRITICAL MQTT COMMANDS.
 * Broker AUTHENTICATION is present: the Mosquitto broker requires a username
 * and password, and this firmware presents the MQTT_USERNAME/MQTT_PASSWORD
 * below to connect. That authentication only establishes that the CONNECTION
 * is a known lab client -- it says nothing about whether a given PUBLISH is
 * allowed to actuate the motor. applyCommand() below obeys any START/STOP it
 * receives on COMMAND_TOPIC with no token, signature, per-command permission,
 * or sender check. So ANY client that can authenticate to the broker and
 * knows the control topic can drive the motor, exactly as the local buttons
 * do. The lesson: authenticating to an MQTT broker does not authorize the
 * individual control commands a client then sends. This is NOT an
 * authentication bypass -- the attacker connects with valid broker
 * credentials; the missing control is per-command AUTHORIZATION on the ESP32.
 *
 * PHYSICAL PIN ASSIGNMENT (validated bench wiring for this panel). The L293D
 * enable pin is tied HIGH in hardware, so run/stop is driven through IN1/IN2
 * and there is no software enable GPIO.
 *
 * LAB-SAFE PLACEHOLDERS ONLY. WIFI_PASSWORD/MQTT_USERNAME/MQTT_PASSWORD below
 * are intentionally fake training-lab values, not real secrets -- this file
 * is committed to the repository and must never carry a genuine credential.
 * Replace them with the actual CyberTrainer AP password and the Mosquitto
 * lab credentials at provisioning time; until those are configured, this
 * sketch cannot authenticate to a real broker.
 */

#include <WiFi.h>
#include <PubSubClient.h>

// --- finalized local training network (placeholders -- see file header) -----
static const char *WIFI_SSID = "CyberTrainer";
// TODO(provisioning): set to the real CyberTrainer AP password before flash.
static const char *WIFI_PASSWORD = "CHANGE_ME_LAB_AP_PASSWORD";

static const char *MQTT_BROKER = "192.168.50.1";
static const uint16_t MQTT_PORT = 1883;

// The broker DOES require authentication -- these are the credentials this
// firmware presents to connect, recoverable from the compiled image exactly
// as the activity's "expected findings" describe. They authorize the
// CONNECTION, not any individual motor command (see file header).
static const char *MQTT_CLIENT_ID = "panel1-motor-controller";
// TODO(provisioning): set to the real Mosquitto lab username/password.
static const char *MQTT_USERNAME = "panel1-device";
static const char *MQTT_PASSWORD = "CHANGE_ME_LAB_MQTT_PASSWORD";

static const char *COMMAND_TOPIC = "cybertrainer/smart-home/motor/control";
static const char *STATE_TOPIC = "cybertrainer/smart-home/motor/state";

// --- physical pin assignment (validated bench wiring, see file header) -------
static const uint8_t START_BUTTON = 32;  // local START push-button (to GND)
static const uint8_t STOP_BUTTON = 33;   // local STOP push-button (to GND)
static const uint8_t MOTOR_IN1 = 25;     // L293D IN1
static const uint8_t MOTOR_IN2 = 26;     // L293D IN2 (EN tied HIGH in hardware)
static const uint8_t GREEN_LED = 27;     // motor running indicator
static const uint8_t RED_LED = 14;       // motor stopped indicator
static const uint8_t BUZZER = 13;        // chirps on a state change

static const unsigned long BUZZER_CHIRP_MS = 150;
static const unsigned long BUTTON_DEBOUNCE_MS = 40;

WiFiClient espClient;
PubSubClient client(espClient);
bool motorRunning = false;

// Drive the L293D and the indicator LEDs to match the requested state. The
// enable pin is jumpered HIGH, so IN1 HIGH / IN2 LOW runs the motor and both
// LOW stops it.
static void setMotorOutputs(bool run) {
  digitalWrite(MOTOR_IN1, run ? HIGH : LOW);
  digitalWrite(MOTOR_IN2, LOW);
  digitalWrite(GREEN_LED, run ? HIGH : LOW);
  digitalWrite(RED_LED, run ? LOW : HIGH);
}

static void chirpBuzzer() {
  digitalWrite(BUZZER, HIGH);
  delay(BUZZER_CHIRP_MS);
  digitalWrite(BUZZER, LOW);
}

// Apply a new motor state, update the indicators, publish the retained state,
// and chirp. Shared by the local buttons and the MQTT command handler.
static void applyMotorState(bool run) {
  motorRunning = run;
  setMotorOutputs(motorRunning);
  client.publish(STATE_TOPIC, motorRunning ? "RUNNING" : "STOPPED", true);
  chirpBuzzer();
}

static void motorStart() {
  applyMotorState(true);
}

static void motorStop() {
  applyMotorState(false);
}

// THE VULNERABLE HANDLER. A START/STOP arriving on COMMAND_TOPIC is obeyed
// based on message content ALONE -- there is no per-command authorization
// check. Broker authentication has already proven the client may connect; it
// does NOT prove the client may command the motor, and this firmware never
// checks. Any authenticated client that knows the topic is therefore obeyed
// exactly like the panel's own local buttons.
static void applyCommand(const String &message) {
  if (message == "START") {
    motorStart();
  } else if (message == "STOP") {
    motorStop();
  }
  // Any other payload reaches the callback and is ignored (no actuation),
  // exactly like the real device.
}

static void onMessage(char *topic, byte *payload, unsigned int length) {
  String message;
  message.reserve(length);
  for (unsigned int i = 0; i < length; i++) {
    message += static_cast<char>(payload[i]);
  }
  message.trim();
  message.toUpperCase();
  if (String(topic) == COMMAND_TOPIC) {
    applyCommand(message);
  }
}

// Poll the local push-buttons (active-low, INPUT_PULLUP). These are the
// LEGITIMATE local command source; the MQTT path above accepts the same
// commands from any authenticated client with no additional authorization.
static void pollButtons() {
  static unsigned long lastEdge = 0;
  if (millis() - lastEdge < BUTTON_DEBOUNCE_MS) {
    return;
  }
  if (digitalRead(START_BUTTON) == LOW && !motorRunning) {
    motorStart();
    lastEdge = millis();
  } else if (digitalRead(STOP_BUTTON) == LOW && motorRunning) {
    motorStop();
    lastEdge = millis();
  }
}

static void ensureConnected() {
  while (!client.connected()) {
    if (client.connect(MQTT_CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
      client.subscribe(COMMAND_TOPIC);
      client.publish(STATE_TOPIC, motorRunning ? "RUNNING" : "STOPPED", true);
    } else {
      delay(1000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(START_BUTTON, INPUT_PULLUP);
  pinMode(STOP_BUTTON, INPUT_PULLUP);
  pinMode(MOTOR_IN1, OUTPUT);
  pinMode(MOTOR_IN2, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  pinMode(BUZZER, OUTPUT);
  setMotorOutputs(false);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(onMessage);
}

void loop() {
  ensureConnected();
  client.loop();
  pollButtons();
}
