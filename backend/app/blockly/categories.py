"""The master catalog's category table, in toolbox order.

Category order is the order a toolbox lists them; groups are contiguous so a
toolbox can separate them. The grouping follows the categories the ESP IDE
block ecosystem uses as a functional reference — see `app/blockly/README.md`.

`hue` is a Blockly hue chosen so the three implemented Arduino categories
(inputs/outputs/time, hue 65) and program (hue 210) match the colours the
existing Blockly POC blocks already use.
"""

from __future__ import annotations

from app.blockly.models import BlockCategory, CategoryGroup

_P = CategoryGroup.PROGRAMMING
_A = CategoryGroup.ARDUINO
_C = CategoryGroup.COMMUNICATION
_H = CategoryGroup.HARDWARE
_S = CategoryGroup.STORAGE

CATEGORIES: tuple[BlockCategory, ...] = (
    BlockCategory("logic", "Logic", "Booleans, comparisons and conditional branching.", _P, 30),
    BlockCategory("loops", "Loops", "Repetition and loop control.", _P, 120),
    BlockCategory("math", "Math", "Numbers, arithmetic and numeric helpers.", _P, 230),
    BlockCategory("text", "Text", "Text literals and text manipulation.", _P, 160),
    BlockCategory("variables", "Variables", "Declaring, reading and changing variables.", _P, 330),
    BlockCategory("functions", "Functions", "Defining and calling reusable functions.", _P, 290),
    BlockCategory("lists", "Lists", "Ordered collections of values.", _P, 260),
    BlockCategory("program", "Program", "Sketch structure: setup, loop, globals and libraries.", _A, 210),
    BlockCategory("inputs", "Inputs", "Reading pins and configuring pin modes.", _A, 65),
    BlockCategory("outputs", "Outputs", "Driving pins: digital, PWM and analog output.", _A, 65),
    BlockCategory("time", "Time", "Delays, timestamps and timed execution.", _A, 65),
    BlockCategory("serial", "Serial", "UART serial communication.", _C, 180),
    BlockCategory("i2c", "I2C", "Two-wire I2C bus communication.", _C, 20),
    BlockCategory("spi", "SPI", "SPI bus communication.", _C, 45),
    BlockCategory("wifi", "Wi-Fi", "Wi-Fi connection and network information.", _C, 195),
    BlockCategory("mqtt", "MQTT", "Generic MQTT client publish/subscribe messaging.", _C, 275),
    BlockCategory("http", "HTTP", "HTTP client requests and responses.", _C, 340),
    BlockCategory("bluetooth", "Bluetooth", "Bluetooth serial communication.", _C, 225),
    BlockCategory("espnow", "ESP-NOW", "Peer-to-peer ESP-NOW messaging.", _C, 145),
    BlockCategory("sensors", "Sensors", "Common sensor readings.", _H, 90),
    BlockCategory("displays", "Displays", "Text output on attached displays.", _H, 315),
    BlockCategory("graphics", "Graphics", "Pixels, lines, rectangles and circles.", _H, 355),
    BlockCategory("motors", "Motors", "DC motors and servos.", _H, 15),
    BlockCategory("neopixel", "NeoPixel", "Addressable RGB LEDs.", _H, 300),
    BlockCategory("charts", "Charts", "Plotting values on a display.", _H, 175),
    BlockCategory(
        "animated_eyes",
        "Animated Eyes",
        "Specialised animated eye expressions on a display.",
        _H,
        250,
        optional=True,
    ),
    BlockCategory("filesystem", "File System", "Reading and writing files on flash storage.", _S, 100),
)
