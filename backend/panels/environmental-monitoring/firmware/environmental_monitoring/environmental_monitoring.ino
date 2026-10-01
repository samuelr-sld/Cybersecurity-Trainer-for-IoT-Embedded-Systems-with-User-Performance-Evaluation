/*
  ESP32 Environmental Monitor + LED Alert + Fan Control
  --------------------------------------------------------
  - DHT11: temperature + relative humidity
  - SSD1306 (I2C, 128x64): live display of readings
  - LED on GPIO 5: blinks when temperature reaches 31 C
  - L293D: drives a fan when temperature reaches 31 C

  Pin connections
  ----------------
  DHT11 DATA   -> GPIO 4   (10k pull-up to 3.3V)
  DHT11 VCC    -> 3.3V
  DHT11 GND    -> GND
  SSD1306 SDA  -> GPIO 21
  SSD1306 SCL  -> GPIO 22
  SSD1306 VCC  -> 3.3V
  SSD1306 GND  -> GND
  LED anode    -> GPIO 5 (through a current-limiting resistor, ~220-330 ohm)
  LED cathode  -> GND
  L293D IN1    -> GPIO 25
  L293D IN2    -> GND (tied low, single direction)
  L293D EN1    -> 3.3V (always enabled when IN1 is high)
  L293D VCC1   -> 3.3V (logic supply)
  L293D VCC2   -> external motor supply (per fan rating)
  L293D GND    -> common GND (ESP32 + motor supply)
  Fan motor    -> OUT1 / OUT2

  Libraries required (Library Manager):
  - DHT sensor library (Adafruit)
  - Adafruit Unified Sensor
  - Adafruit SSD1306
  - Adafruit GFX Library
*/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>

// ---------- Pin definitions ----------
#define DHTPIN 4
#define DHTTYPE DHT11
#define LED_PIN 5
#define FAN_PIN 25

// ---------- OLED configuration ----------
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
DHT dht(DHTPIN, DHTTYPE);

const unsigned long READ_INTERVAL = 2000; // DHT11 min read interval
unsigned long lastReadTime = 0;

const float TEMP_THRESHOLD = 31.0; // degrees Celsius
const unsigned long BLINK_INTERVAL = 500; // LED blink rate in ms
unsigned long lastBlinkTime = 0;
bool ledState = false;
bool alertActive = false;

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  pinMode(FAN_PIN, OUTPUT);
  digitalWrite(FAN_PIN, LOW);

  dht.begin();

  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    while (true) { delay(10); } // halt if display not found
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("Initializing..."));
  display.display();
  delay(1000);
}

void loop() {
  unsigned long now = millis();

  if (now - lastReadTime >= READ_INTERVAL) {
    lastReadTime = now;

    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius

    if (isnan(humidity) || isnan(temperature)) {
      Serial.println(F("Failed to read from DHT11 sensor"));
      showError();
      return;
    }

    alertActive = (temperature >= TEMP_THRESHOLD);

    // Fan follows the same threshold as the LED alert
    digitalWrite(FAN_PIN, alertActive ? HIGH : LOW);

    if (!alertActive) {
      digitalWrite(LED_PIN, LOW);
      ledState = false;
    }

    Serial.print(F("Temp: "));
    Serial.print(temperature);
    Serial.print(F(" C  Humidity: "));
    Serial.print(humidity);
    Serial.print(F(" %  Alert: "));
    Serial.print(alertActive ? F("YES") : F("NO"));
    Serial.print(F("  Fan: "));
    Serial.println(alertActive ? F("ON") : F("OFF"));

    updateDisplay(temperature, humidity, alertActive);
  }

  // Non-blocking LED blink while alert is active
  if (alertActive && (now - lastBlinkTime >= BLINK_INTERVAL)) {
    lastBlinkTime = now;
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState ? HIGH : LOW);
  }
}

void updateDisplay(float temperature, float humidity, bool alert) {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);

  display.println(F("Env Monitor"));
  display.println();

  display.setTextSize(2);
  display.print(F("T: "));
  display.print(temperature, 1);
  display.println(F(" C"));

  display.print(F("H: "));
  display.print(humidity, 1);
  display.println(F(" %"));

  display.setTextSize(1);
  display.println();
  display.print(F("Fan: "));
  display.println(alert ? F("ON") : F("OFF"));

  display.display();
}

void showError() {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.println(F("Sensor read error"));
  display.println(F("Check DHT11 wiring"));
  display.display();
}
