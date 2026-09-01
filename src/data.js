export const SCENARIOS = [
  'Weak MQTT Auth',
  'GPIO Control Abuse',
  'TLS Misconfiguration',
  'Firmware OTA Abuse',
  'Hardcoded Secrets',
]

const FIRST = [
  'Juan', 'Maria', 'Luis', 'Ana', 'Carlo', 'Sofia', 'Miguel', 'Lara',
  'Diego', 'Isabel', 'Rafael', 'Nina', 'Paolo', 'Camille', 'Andres', 'Elena',
]
const LAST = [
  'Dela Cruz', 'Santos', 'Reyes', 'Garcia', 'Ramos', 'Torres', 'Cruz', 'Flores',
  'Mendoza', 'Navarro', 'Bautista', 'Villanueva', 'Castro', 'Domingo', 'Perez',
]

const STATUSES = ['COMPLETE', 'IN PROGRESS', 'NOT STARTED']
const ACTIVITIES = [
  'Today, 10:56 AM',
  'Today, 9:14 AM',
  'Yesterday, 4:22 PM',
  'Yesterday, 11:03 AM',
  'Aug 29, 2:40 PM',
  'Aug 28, 8:05 AM',
]

function seeded(i) {
  return {
    name: i === 0 ? 'Juan Dela Cruz' : `${FIRST[i % FIRST.length]} ${LAST[(i * 3) % LAST.length]}`,
    id: i === 0 ? '2021-04213' : `2021-${String(42000 + i).padStart(5, '0')}`,
    scenario: SCENARIOS[i % SCENARIOS.length],
    status: i === 0 ? 'IN PROGRESS' : STATUSES[i % STATUSES.length],
    lastActivity: i === 0 ? 'Today, 10:56 AM' : ACTIVITIES[i % ACTIVITIES.length],
    metrics: {
      attackCompletion: i === 0 ? 78 : 40 + ((i * 17) % 55),
      timeToExploit: i === 0 ? '00:04:12' : `00:0${(i % 9) + 1}:${String((i * 11) % 60).padStart(2, '0')}`,
      timeToResolution: i === 0 ? '00:11:47' : `00:${String(8 + (i % 20)).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`,
      attempts: i === 0 ? 4 : 1 + (i % 6),
      debugIndex: i === 0 ? 0.71 : Number((0.35 + ((i * 13) % 50) / 100).toFixed(2)),
      reconEff: i === 0 ? 82 : 50 + ((i * 9) % 45),
      bars: i === 0 ? [82, 96, 88, 54, 28] : [60 + (i % 30), 90 - (i % 20), 70 - (i % 25), 50 - (i % 15), 30 + (i % 20)],
      trend: i === 0 ? [18, 16, 14, 15, 12, 10, 8] : [20 - (i % 5), 18, 15, 16, 12, 11, 9],
      radar: i === 0 ? [0.9, 0.85, 0.55, 0.4, 0.7] : [0.5 + (i % 5) / 10, 0.6, 0.45, 0.5, 0.65],
    },
    log:
      i === 0
        ? [
            { time: '10:41:02', phase: 'Hack', action: 'nmap scan on broker', result: 'OK' },
            { time: '10:42:05', phase: 'Hack', action: 'Subscribed to # topic', result: 'OK' },
            { time: '10:44:10', phase: 'Hack', action: 'Publish forged payload (attempt 4)', result: 'SUCCESS' },
            { time: '10:52:30', phase: 'Build', action: 'Compile revised firmware (attempt 1)', result: 'ERROR' },
            { time: '10:55:12', phase: 'Build', action: 'Compile revised firmware (attempt 2)', result: 'OK' },
            { time: '10:56:40', phase: 'Build', action: 'Flash + validation test', result: 'SECURED' },
          ]
        : [
            { time: '09:12:01', phase: 'Hack', action: 'Started recon on sandbox broker', result: 'OK' },
            { time: '09:18:44', phase: 'Hack', action: 'Reviewed open MQTT port 1883', result: 'OK' },
            { time: '09:40:10', phase: 'Build', action: 'Opened remediation sketch', result: 'OK' },
          ],
  }
}

export const STUDENTS = Array.from({ length: 48 }, (_, i) => seeded(i))

export const PROJECT_FILES = {
  'main.ino': `/* Weak MQTT Auth lab sketch — ESP32
 * Educational sandbox firmware. Add authentication, ACL, and TLS.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include "mqtt_config.h"
#include "wifi_secrets.h"

WiFiClient wifi;
PubSubClient mqtt(wifi);

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  // TODO: set username/password before connect
  mqtt.connect("sandbox-node");
  mqtt.subscribe("sandbox/mqtt/#");
}

void loop() {
  mqtt.loop();
  mqtt.publish("sandbox/mqtt/telemetry", "temp=24.1");
  delay(2000);
}
`,
  'mqtt_config.h': `#pragma once
#define MQTT_BROKER "192.168.4.1"
#define MQTT_PORT   1883
// TODO: switch to 8883 and enable TLS
// TODO: restrict subscribe/publish ACLs
`,
  'wifi_secrets.h': `#pragma once
#define WIFI_SSID "SANDBOX-LAB"
#define WIFI_PASS "isolated-only"
`,
  'README.md': `# IoT MQTT Comm Panel

Remediate Weak MQTT Auth:
1. Add client authentication
2. Restrict topic ACL
3. Use TLS for the broker link
`,
}

export const GUIDED_STEPS = [
  'Discover broker IP',
  'Subscribe to # wildcard',
  'Publish forged telemetry',
]

export const REMEDIATION_ITEMS = [
  'Add client authentication',
  'Restrict topic ACL',
  'Use TLS for broker link',
]
