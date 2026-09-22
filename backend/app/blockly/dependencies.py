"""The closed vocabulary of header/library dependencies a block may declare.

Blocks name a dependency by id, never by a header string, so a typo is a
catalog error rather than a silently missing `#include` later. Turning an id
into an actual include or library install is a generator-phase concern.
"""

from __future__ import annotations

from app.blockly.models import CatalogError, DependencyProvider, LibraryDependency

_CORE = DependencyProvider.ESP32_CORE
_LIBRARY = DependencyProvider.ARDUINO_LIBRARY

_ALL: tuple[LibraryDependency, ...] = (
    LibraryDependency("wire", "Wire (I2C)", "Wire.h", _CORE),
    LibraryDependency("spi", "SPI", "SPI.h", _CORE),
    LibraryDependency("wifi", "WiFi", "WiFi.h", _CORE),
    LibraryDependency("http_client", "HTTPClient", "HTTPClient.h", _CORE),
    LibraryDependency("bluetooth_serial", "BluetoothSerial", "BluetoothSerial.h", _CORE),
    LibraryDependency("esp_now", "ESP-NOW", "esp_now.h", _CORE),
    LibraryDependency("littlefs", "LittleFS", "LittleFS.h", _CORE),
    LibraryDependency("pubsubclient", "PubSubClient (MQTT)", "PubSubClient.h", _LIBRARY),
    LibraryDependency("dht", "DHT sensor library", "DHT.h", _LIBRARY),
    LibraryDependency("esp32_servo", "ESP32Servo", "ESP32Servo.h", _LIBRARY),
    LibraryDependency("adafruit_neopixel", "Adafruit NeoPixel", "Adafruit_NeoPixel.h", _LIBRARY),
    LibraryDependency("adafruit_gfx", "Adafruit GFX", "Adafruit_GFX.h", _LIBRARY),
    LibraryDependency("adafruit_ssd1306", "Adafruit SSD1306", "Adafruit_SSD1306.h", _LIBRARY),
)


def _index(items: tuple[LibraryDependency, ...]) -> dict[str, LibraryDependency]:
    indexed: dict[str, LibraryDependency] = {}
    for item in items:
        if item.dependency_id in indexed:
            raise CatalogError(f"duplicate dependency id: {item.dependency_id}")
        indexed[item.dependency_id] = item
    return indexed


DEPENDENCIES: dict[str, LibraryDependency] = _index(_ALL)
