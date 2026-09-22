"""Hardware blocks: sensors, displays, graphics, motors, NeoPixel, charts, animated eyes.

Sensors are catalogued as generic concepts ("a temperature reading"), not as
one specific part; choosing a driver is a later concern. Animated eyes are a
specialised, optional category (`BlockCategory.optional`).
"""

from __future__ import annotations

from app.blockly.definitions.factory import BOOLEAN, NUMBER, PIN, TEXT, CategoryFactory
from app.blockly.models import Capability

_sensors = CategoryFactory("sensors")
_displays = CategoryFactory("displays")
_graphics = CategoryFactory("graphics")
_motors = CategoryFactory("motors")
_neopixel = CategoryFactory("neopixel")
_charts = CategoryFactory("charts")
_eyes = CategoryFactory("animated_eyes")

_I2C = (Capability.I2C,)
_DISPLAY = (Capability.DISPLAY,)
_GFX = ("adafruit_gfx",)
_OLED = ("wire", "adafruit_gfx", "adafruit_ssd1306")

SENSORS = (
    _sensors.expression(
        "sensor.digital_read", "digital sensor", "Reads a digital sensor on a pin.", BOOLEAN, (("PIN", PIN),),
        caps=(Capability.GPIO,),
    ),
    _sensors.expression(
        "sensor.analog_read", "analog sensor", "Reads an analog sensor on a pin.", NUMBER, (("PIN", PIN),),
        caps=(Capability.ANALOG_INPUT,),
    ),
    _sensors.expression(
        "sensor.dht_temperature", "DHT temperature", "Temperature from a DHT sensor.", NUMBER, (("PIN", PIN),),
        deps=("dht",), caps=(Capability.GPIO,),
    ),
    _sensors.expression(
        "sensor.dht_humidity", "DHT humidity", "Relative humidity from a DHT sensor.", NUMBER, (("PIN", PIN),),
        deps=("dht",), caps=(Capability.GPIO,),
    ),
    _sensors.expression(
        "sensor.ultrasonic_distance",
        "ultrasonic distance",
        "Distance from an ultrasonic ranger.",
        NUMBER,
        (("TRIGGER", PIN), ("ECHO", PIN)),
        caps=(Capability.GPIO,),
    ),
    _sensors.expression(
        "sensor.light", "light level", "Ambient light reading.", NUMBER, (("PIN", PIN),), caps=(Capability.ANALOG_INPUT,)
    ),
    _sensors.value("sensor.temperature", "temperature", "A temperature reading.", NUMBER, deps=("wire",), caps=_I2C),
    _sensors.value("sensor.humidity", "humidity", "A relative humidity reading.", NUMBER, deps=("wire",), caps=_I2C),
    _sensors.value("sensor.pressure", "pressure", "An air pressure reading.", NUMBER, deps=("wire",), caps=_I2C),
    _sensors.expression(
        "sensor.accelerometer", "accelerometer", "Acceleration on an axis.", NUMBER, (("AXIS", TEXT),),
        deps=("wire",), caps=_I2C,
    ),
    _sensors.expression(
        "sensor.gyroscope", "gyroscope", "Angular rate on an axis.", NUMBER, (("AXIS", TEXT),), deps=("wire",), caps=_I2C
    ),
    _sensors.expression(
        "sensor.color", "color sensor", "A colour channel reading.", NUMBER, (("CHANNEL", TEXT),),
        deps=("wire",), caps=_I2C,
    ),
)

DISPLAYS = (
    _displays.statement(
        "display.initialize", "display initialize", "Initialises a display of a given size.",
        (("WIDTH", NUMBER), ("HEIGHT", NUMBER), ("ADDRESS", NUMBER)), deps=_OLED, caps=_DISPLAY,
    ),
    _displays.statement("display.clear", "display clear", "Clears the display buffer.", deps=_OLED, caps=_DISPLAY),
    _displays.statement(
        "display.set_cursor", "set cursor", "Moves the text cursor.", (("X", NUMBER), ("Y", NUMBER)),
        deps=_OLED, caps=_DISPLAY,
    ),
    _displays.statement(
        "display.print", "display print", "Prints a value at the cursor.", (("VALUE", TEXT),), deps=_OLED, caps=_DISPLAY
    ),
    _displays.statement(
        "display.println", "display println", "Prints a value then moves to the next line.", (("VALUE", TEXT),),
        deps=_OLED, caps=_DISPLAY,
    ),
    _displays.statement("display.show", "display update", "Pushes the buffer to the screen.", deps=_OLED, caps=_DISPLAY),
    _displays.statement(
        "display.text_size", "text size", "Sets the text size.", (("SIZE", NUMBER),), deps=_OLED, caps=_DISPLAY
    ),
    _displays.statement(
        "display.text_color", "text color", "Sets the text colour.", (("COLOR", NUMBER),), deps=_OLED, caps=_DISPLAY
    ),
)

_XY_COLOR = (("X", NUMBER), ("Y", NUMBER), ("COLOR", NUMBER))
_RECT = (("X", NUMBER), ("Y", NUMBER), ("WIDTH", NUMBER), ("HEIGHT", NUMBER), ("COLOR", NUMBER))
_CIRCLE = (("X", NUMBER), ("Y", NUMBER), ("RADIUS", NUMBER), ("COLOR", NUMBER))

GRAPHICS = (
    _graphics.statement("graphics.draw_pixel", "draw pixel", "Draws one pixel.", _XY_COLOR, deps=_GFX, caps=_DISPLAY),
    _graphics.statement(
        "graphics.draw_line", "draw line", "Draws a line between two points.",
        (("X0", NUMBER), ("Y0", NUMBER), ("X1", NUMBER), ("Y1", NUMBER), ("COLOR", NUMBER)), deps=_GFX, caps=_DISPLAY,
    ),
    _graphics.statement("graphics.draw_rect", "draw rectangle", "Draws a rectangle outline.", _RECT, deps=_GFX, caps=_DISPLAY),
    _graphics.statement("graphics.fill_rect", "fill rectangle", "Draws a filled rectangle.", _RECT, deps=_GFX, caps=_DISPLAY),
    _graphics.statement("graphics.draw_circle", "draw circle", "Draws a circle outline.", _CIRCLE, deps=_GFX, caps=_DISPLAY),
    _graphics.statement("graphics.fill_circle", "fill circle", "Draws a filled circle.", _CIRCLE, deps=_GFX, caps=_DISPLAY),
)

MOTORS = (
    _motors.statement(
        "motor.initialize", "motor initialize", "Configures the pins driving a motor.",
        (("PIN_A", PIN), ("PIN_B", PIN), ("ENABLE", PIN)), caps=(Capability.MOTOR_DRIVER, Capability.GPIO),
    ),
    _motors.statement("motor.start", "motor start", "Starts a motor.", caps=(Capability.MOTOR_DRIVER,)),
    _motors.statement("motor.stop", "motor stop", "Stops a motor.", caps=(Capability.MOTOR_DRIVER,)),
    _motors.statement(
        "motor.set_speed", "motor speed", "Sets a motor's speed.", (("SPEED", NUMBER),), caps=(Capability.MOTOR_DRIVER,)
    ),
    _motors.statement(
        "motor.set_direction", "motor direction", "Sets a motor's direction.", (("FORWARD", BOOLEAN),),
        caps=(Capability.MOTOR_DRIVER,),
    ),
    _motors.statement(
        "motor.set_pwm", "motor PWM", "Sets the PWM duty driving a motor.", (("DUTY", NUMBER),),
        caps=(Capability.MOTOR_DRIVER, Capability.PWM_OUTPUT),
    ),
    _motors.statement(
        "servo.attach", "servo attach", "Attaches a servo to a pin.", (("PIN", PIN),),
        deps=("esp32_servo",), caps=(Capability.SERVO,),
    ),
    _motors.statement(
        "servo.write", "servo write", "Moves a servo to an angle.", (("ANGLE", NUMBER),),
        deps=("esp32_servo",), caps=(Capability.SERVO,),
    ),
    _motors.statement(
        "servo.detach", "servo detach", "Releases a servo.", deps=("esp32_servo",), caps=(Capability.SERVO,)
    ),
)

_NEO = ("adafruit_neopixel",)
_NEO_CAP = (Capability.NEOPIXEL,)

NEOPIXEL = (
    _neopixel.statement(
        "neopixel.initialize", "NeoPixel initialize", "Configures a strip's pin and length.",
        (("PIN", PIN), ("COUNT", NUMBER)), deps=_NEO, caps=_NEO_CAP,
    ),
    _neopixel.statement(
        "neopixel.set_pixel", "set pixel", "Sets one pixel's colour.", (("INDEX", NUMBER), ("COLOR", NUMBER)),
        deps=_NEO, caps=_NEO_CAP,
    ),
    _neopixel.statement("neopixel.set_all", "set all pixels", "Sets every pixel to one colour.", (("COLOR", NUMBER),), deps=_NEO, caps=_NEO_CAP),
    _neopixel.expression(
        "neopixel.rgb", "RGB color", "Combines red, green and blue into a colour.", NUMBER,
        (("RED", NUMBER), ("GREEN", NUMBER), ("BLUE", NUMBER)), deps=_NEO, caps=_NEO_CAP,
    ),
    _neopixel.statement("neopixel.set_brightness", "brightness", "Sets strip brightness.", (("LEVEL", NUMBER),), deps=_NEO, caps=_NEO_CAP),
    _neopixel.statement("neopixel.show", "show", "Pushes pixel colours to the strip.", deps=_NEO, caps=_NEO_CAP),
    _neopixel.statement("neopixel.clear", "clear", "Turns every pixel off.", deps=_NEO, caps=_NEO_CAP),
)

CHARTS = (
    _charts.statement("chart.initialize", "chart initialize", "Creates a chart region on a display.", (("WIDTH", NUMBER), ("HEIGHT", NUMBER)), deps=_GFX, caps=_DISPLAY),
    _charts.statement("chart.add_value", "add value", "Appends a value to the chart.", (("VALUE", NUMBER),), deps=_GFX, caps=_DISPLAY),
    _charts.statement("chart.set_range", "set range", "Sets the chart's value range.", (("MIN", NUMBER), ("MAX", NUMBER)), deps=_GFX, caps=_DISPLAY),
    _charts.statement("chart.update", "update chart", "Redraws the chart.", deps=_GFX, caps=_DISPLAY),
    _charts.statement("chart.clear", "clear chart", "Removes every plotted value.", deps=_GFX, caps=_DISPLAY),
)

EYES = (
    _eyes.statement("eyes.initialize", "eyes initialize", "Prepares the animated eyes.", deps=_GFX, caps=_DISPLAY),
    _eyes.statement("eyes.set_expression", "eye expression", "Sets the eyes' expression.", (("EXPRESSION", TEXT),), deps=_GFX, caps=_DISPLAY),
    _eyes.statement("eyes.look", "look direction", "Points the eyes in a direction.", (("DIRECTION", TEXT),), deps=_GFX, caps=_DISPLAY),
    _eyes.statement("eyes.blink", "blink", "Blinks the eyes once.", deps=_GFX, caps=_DISPLAY),
    _eyes.statement("eyes.animate", "eye animation", "Plays a named animation.", (("ANIMATION", TEXT),), deps=_GFX, caps=_DISPLAY),
    _eyes.statement("eyes.update", "update eyes", "Advances and redraws the eyes.", deps=_GFX, caps=_DISPLAY),
)

BLOCKS = SENSORS + DISPLAYS + GRAPHICS + MOTORS + NEOPIXEL + CHARTS + EYES
