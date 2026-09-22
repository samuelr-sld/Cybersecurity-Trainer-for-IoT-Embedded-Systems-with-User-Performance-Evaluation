"""Communication blocks: serial, I2C, SPI, Wi-Fi, MQTT, HTTP, Bluetooth, ESP-NOW.

These describe generic capabilities. In particular the MQTT blocks say
nothing about authentication or authorisation policy: `mqtt.publish` is just
publishing. Any security-relevant behaviour belongs to a project's firmware
logic in a later phase, never to a block definition.
"""

from __future__ import annotations

from app.blockly.definitions.factory import ANY, BODY, BOOLEAN, NUMBER, PIN, TEXT, CategoryFactory
from app.blockly.models import Capability

_serial = CategoryFactory("serial")
_i2c = CategoryFactory("i2c")
_spi = CategoryFactory("spi")
_wifi = CategoryFactory("wifi")
_mqtt = CategoryFactory("mqtt")
_http = CategoryFactory("http")
_bluetooth = CategoryFactory("bluetooth")
_espnow = CategoryFactory("espnow")

_SER = (Capability.SERIAL,)
_I2C = (Capability.I2C,)
_SPI = (Capability.SPI,)
_WIFI = (Capability.WIFI,)
_MQTT = (Capability.WIFI, Capability.MQTT)
_HTTP = (Capability.WIFI, Capability.HTTP)
_BT = (Capability.BLUETOOTH,)
_NOW = (Capability.WIFI, Capability.ESP_NOW)

SERIAL = (
    _serial.statement("serial.begin", "serial begin", "Starts serial at a baud rate.", (("BAUD", NUMBER),), caps=_SER),
    _serial.statement("serial.print", "serial print", "Prints a value to serial.", (("VALUE", ANY),), caps=_SER),
    _serial.statement(
        "serial.println", "serial println", "Prints a value followed by a newline.", (("VALUE", ANY),), caps=_SER
    ),
    _serial.value("serial.available", "serial available", "Number of bytes waiting to be read.", NUMBER, caps=_SER),
    _serial.value("serial.read", "serial read", "Reads one byte from serial.", NUMBER, caps=_SER),
    _serial.value("serial.read_string", "serial read string", "Reads incoming serial data as text.", TEXT, caps=_SER),
    _serial.value("serial.read_char", "serial read character", "Reads one character from serial.", TEXT, caps=_SER),
    _serial.value("serial.parse_number", "serial parse number", "Reads the next number from serial.", NUMBER, caps=_SER),
    _serial.statement("serial.flush", "serial flush", "Waits for outgoing serial data to be sent.", caps=_SER),
)

I2C = (
    _i2c.statement(
        "i2c.begin",
        "I2C begin",
        "Starts the I2C bus on the given pins.",
        (("SDA", PIN), ("SCL", PIN)),
        deps=("wire",),
        caps=_I2C,
    ),
    _i2c.statement(
        "i2c.begin_transmission",
        "I2C begin transmission",
        "Starts a transmission to a device address.",
        (("ADDRESS", NUMBER),),
        deps=("wire",),
        caps=_I2C,
    ),
    _i2c.statement("i2c.write", "I2C write", "Queues a byte to send.", (("DATA", NUMBER),), deps=("wire",), caps=_I2C),
    _i2c.statement(
        "i2c.request",
        "I2C request",
        "Requests a number of bytes from a device.",
        (("ADDRESS", NUMBER), ("COUNT", NUMBER)),
        deps=("wire",),
        caps=_I2C,
    ),
    _i2c.value("i2c.read", "I2C read", "Reads one received byte.", NUMBER, deps=("wire",), caps=_I2C),
    _i2c.statement(
        "i2c.end_transmission", "I2C end transmission", "Sends the queued bytes.", deps=("wire",), caps=_I2C
    ),
)

SPI = (
    _spi.statement("spi.begin", "SPI begin", "Starts the SPI bus.", deps=("spi",), caps=_SPI),
    _spi.expression(
        "spi.transfer", "SPI transfer", "Sends a byte and returns the byte received.", NUMBER, (("DATA", NUMBER),),
        deps=("spi",), caps=_SPI,
    ),
    _spi.value("spi.read", "SPI read", "Reads one byte from the SPI bus.", NUMBER, deps=("spi",), caps=_SPI),
    _spi.statement("spi.write", "SPI write", "Writes one byte to the SPI bus.", (("DATA", NUMBER),), deps=("spi",), caps=_SPI),
)

WIFI = (
    _wifi.statement(
        "wifi.connect",
        "Wi-Fi connect",
        "Connects to a Wi-Fi network.",
        (("SSID", TEXT), ("PASSWORD", TEXT)),
        deps=("wifi",),
        caps=_WIFI,
    ),
    _wifi.statement("wifi.disconnect", "Wi-Fi disconnect", "Disconnects from Wi-Fi.", deps=("wifi",), caps=_WIFI),
    _wifi.value("wifi.status", "Wi-Fi status", "The current Wi-Fi connection status code.", NUMBER, deps=("wifi",), caps=_WIFI),
    _wifi.value("wifi.local_ip", "local IP", "The device's IP address as text.", TEXT, deps=("wifi",), caps=_WIFI),
    _wifi.value("wifi.rssi", "RSSI", "Signal strength of the connection.", NUMBER, deps=("wifi",), caps=_WIFI),
    _wifi.value("wifi.ssid", "SSID", "A network name value.", TEXT, inputs=(("VALUE", TEXT),), deps=("wifi",), caps=_WIFI),
    _wifi.value(
        "wifi.password", "Wi-Fi password", "A network password value.", TEXT, inputs=(("VALUE", TEXT),),
        deps=("wifi",), caps=_WIFI,
    ),
    _wifi.value(
        "wifi.hostname", "hostname", "A device hostname value.", TEXT, inputs=(("VALUE", TEXT),), deps=("wifi",), caps=_WIFI
    ),
)

_MQTT_DEPS = ("wifi", "pubsubclient")

MQTT = (
    _mqtt.statement(
        "mqtt.initialize",
        "MQTT initialize",
        "Configures the MQTT client's broker address and port.",
        (("BROKER", TEXT), ("PORT", NUMBER)),
        deps=_MQTT_DEPS,
        caps=_MQTT,
    ),
    _mqtt.statement(
        "mqtt.connect",
        "MQTT connect",
        "Connects to the broker with a client id and optional credentials.",
        (("CLIENT_ID", TEXT), ("USERNAME", TEXT), ("PASSWORD", TEXT)),
        deps=_MQTT_DEPS,
        caps=_MQTT,
    ),
    _mqtt.statement("mqtt.disconnect", "MQTT disconnect", "Disconnects from the broker.", deps=_MQTT_DEPS, caps=_MQTT),
    _mqtt.value("mqtt.connected", "MQTT connected", "True while connected to the broker.", BOOLEAN, deps=_MQTT_DEPS, caps=_MQTT),
    _mqtt.statement("mqtt.loop", "MQTT loop", "Services the client: keep-alive and incoming messages.", deps=_MQTT_DEPS, caps=_MQTT),
    _mqtt.statement(
        "mqtt.subscribe", "MQTT subscribe", "Subscribes to a topic.", (("TOPIC", TEXT),), deps=_MQTT_DEPS, caps=_MQTT
    ),
    _mqtt.statement(
        "mqtt.publish",
        "MQTT publish",
        "Publishes a payload to a topic.",
        (("TOPIC", TEXT), ("PAYLOAD", TEXT)),
        deps=_MQTT_DEPS,
        caps=_MQTT,
    ),
    _mqtt.container(
        "mqtt.callback",
        "MQTT callback",
        "Runs a body for each received message.",
        (("TOPIC", TEXT), ("PAYLOAD", TEXT), ("DO", BODY)),
        deps=_MQTT_DEPS,
        caps=_MQTT,
    ),
    _mqtt.value("mqtt.topic", "topic", "A topic string value.", TEXT, inputs=(("VALUE", TEXT),), deps=_MQTT_DEPS, caps=_MQTT),
    _mqtt.value(
        "mqtt.payload", "payload", "A message payload value.", TEXT, inputs=(("VALUE", TEXT),), deps=_MQTT_DEPS, caps=_MQTT
    ),
    _mqtt.value(
        "mqtt.broker_address", "broker address", "A broker host name or IP value.", TEXT, inputs=(("VALUE", TEXT),),
        deps=_MQTT_DEPS, caps=_MQTT,
    ),
    _mqtt.value("mqtt.port", "broker port", "A broker TCP port value.", NUMBER, inputs=(("VALUE", NUMBER),), deps=_MQTT_DEPS, caps=_MQTT),
    _mqtt.value(
        "mqtt.username", "MQTT username", "A broker username value.", TEXT, inputs=(("VALUE", TEXT),), deps=_MQTT_DEPS, caps=_MQTT
    ),
    _mqtt.value(
        "mqtt.password", "MQTT password", "A broker password value.", TEXT, inputs=(("VALUE", TEXT),), deps=_MQTT_DEPS, caps=_MQTT
    ),
)

_HTTP_DEPS = ("wifi", "http_client")

HTTP = (
    _http.statement("http.get", "HTTP GET", "Sends a GET request.", (("URL", TEXT),), deps=_HTTP_DEPS, caps=_HTTP),
    _http.statement(
        "http.post", "HTTP POST", "Sends a POST request with a body.", (("URL", TEXT), ("BODY", TEXT)), deps=_HTTP_DEPS, caps=_HTTP
    ),
    _http.statement(
        "http.put", "HTTP PUT", "Sends a PUT request with a body.", (("URL", TEXT), ("BODY", TEXT)), deps=_HTTP_DEPS, caps=_HTTP
    ),
    _http.statement("http.delete", "HTTP DELETE", "Sends a DELETE request.", (("URL", TEXT),), deps=_HTTP_DEPS, caps=_HTTP),
    _http.value("http.response_status", "response status", "The last response's status code.", NUMBER, deps=_HTTP_DEPS, caps=_HTTP),
    _http.value("http.response_body", "response body", "The last response's body as text.", TEXT, deps=_HTTP_DEPS, caps=_HTTP),
    _http.statement(
        "http.set_header",
        "set header",
        "Adds a header to the next request.",
        (("NAME", TEXT), ("VALUE", TEXT)),
        deps=_HTTP_DEPS,
        caps=_HTTP,
    ),
)

BLUETOOTH = (
    _bluetooth.statement(
        "bluetooth.initialize", "Bluetooth initialize", "Starts Bluetooth with a device name.", (("NAME", TEXT),),
        deps=("bluetooth_serial",), caps=_BT,
    ),
    _bluetooth.statement(
        "bluetooth.connect", "Bluetooth connect", "Connects to a device.", (("ADDRESS", TEXT),),
        deps=("bluetooth_serial",), caps=_BT,
    ),
    _bluetooth.statement(
        "bluetooth.disconnect", "Bluetooth disconnect", "Disconnects the current device.",
        deps=("bluetooth_serial",), caps=_BT,
    ),
    _bluetooth.statement(
        "bluetooth.send", "Bluetooth send", "Sends data to the connected device.", (("DATA", TEXT),),
        deps=("bluetooth_serial",), caps=_BT,
    ),
    _bluetooth.value(
        "bluetooth.receive", "Bluetooth receive", "Reads received data as text.", TEXT, deps=("bluetooth_serial",), caps=_BT
    ),
    _bluetooth.value(
        "bluetooth.available", "Bluetooth available", "Number of bytes waiting to be read.", NUMBER,
        deps=("bluetooth_serial",), caps=_BT,
    ),
)

ESPNOW = (
    _espnow.statement("espnow.initialize", "ESP-NOW initialize", "Starts ESP-NOW.", deps=("wifi", "esp_now"), caps=_NOW),
    _espnow.statement(
        "espnow.add_peer", "ESP-NOW add peer", "Registers a peer by MAC address.", (("MAC", TEXT),),
        deps=("wifi", "esp_now"), caps=_NOW,
    ),
    _espnow.statement(
        "espnow.send", "ESP-NOW send", "Sends data to a peer.", (("MAC", TEXT), ("DATA", TEXT)),
        deps=("wifi", "esp_now"), caps=_NOW,
    ),
    _espnow.container(
        "espnow.on_receive", "ESP-NOW receive", "Runs a body for each received message.", (("DATA", TEXT), ("DO", BODY)),
        deps=("wifi", "esp_now"), caps=_NOW,
    ),
)

BLOCKS = SERIAL + I2C + SPI + WIFI + MQTT + HTTP + BLUETOOTH + ESPNOW
