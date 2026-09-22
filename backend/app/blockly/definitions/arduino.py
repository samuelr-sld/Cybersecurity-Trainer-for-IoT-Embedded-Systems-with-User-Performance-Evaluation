"""Arduino/ESP32 blocks: program structure, inputs, outputs and time.

The five blocks the existing Blockly POC already builds (`arduino_setup`,
`arduino_loop`, `pinmode`, `digitalwrite`, `delay` in `src/blockly/`) are the
only IMPLEMENTED entries in the whole catalog. They keep their original
Blockly types via `implemented_as`; the catalog ids are the new stable names.

Pin operands use the `PIN` value type. Nothing here knows which pins a
particular panel wires to what.
"""

from __future__ import annotations

from app.blockly.definitions.factory import ANY, BODY, BOOLEAN, NUMBER, PIN, TEXT, CategoryFactory
from app.blockly.models import Capability

_program = CategoryFactory("program")
_inputs = CategoryFactory("inputs")
_outputs = CategoryFactory("outputs")
_time = CategoryFactory("time")

PROGRAM = (
    _program.container(
        "program.setup",
        "setup",
        "Runs once at start-up: void setup().",
        (("DO", BODY),),
        implemented_as="arduino_setup",
    ),
    _program.container(
        "program.loop",
        "loop",
        "Runs repeatedly forever: void loop().",
        (("DO", BODY),),
        implemented_as="arduino_loop",
    ),
    _program.statement(
        "program.global_declaration",
        "global declaration",
        "Declares a global variable or named constant outside setup and loop.",
        (("NAME", TEXT), ("TYPE", TEXT), ("VALUE", ANY), ("CONSTANT", BOOLEAN)),
    ),
    _program.statement(
        "program.include",
        "include library",
        "Declares that the sketch depends on a library or header.",
        (("LIBRARY", TEXT),),
    ),
)

INPUTS = (
    _inputs.expression(
        "gpio.digital_read",
        "digital read",
        "Reads a digital pin as HIGH (true) or LOW (false).",
        BOOLEAN,
        (("PIN", PIN),),
        caps=(Capability.GPIO,),
    ),
    _inputs.expression(
        "gpio.analog_read",
        "analog read",
        "Reads an analog pin as a number.",
        NUMBER,
        (("PIN", PIN),),
        caps=(Capability.ANALOG_INPUT,),
    ),
    _inputs.statement(
        "gpio.pin_mode",
        "pin mode",
        "Configures a pin as OUTPUT, INPUT or INPUT_PULLUP.",
        (("PIN", PIN), ("MODE", TEXT, "OUTPUT, INPUT or INPUT_PULLUP")),
        caps=(Capability.GPIO,),
        implemented_as="pinmode",
    ),
    _inputs.statement(
        "gpio.pin_mode_input",
        "pin as input",
        "Configures a pin as a plain input.",
        (("PIN", PIN),),
        op="gpio.pin_mode",
        caps=(Capability.GPIO,),
    ),
    _inputs.statement(
        "gpio.pin_mode_input_pullup",
        "pin as input with pull-up",
        "Configures a pin as an input with its internal pull-up resistor enabled.",
        (("PIN", PIN),),
        op="gpio.pin_mode",
        caps=(Capability.GPIO,),
    ),
)

OUTPUTS = (
    _outputs.statement(
        "gpio.digital_write",
        "digital write",
        "Drives a digital pin HIGH (true) or LOW (false).",
        (("PIN", PIN), ("VALUE", BOOLEAN, "HIGH (true) or LOW (false)")),
        caps=(Capability.GPIO,),
        implemented_as="digitalwrite",
    ),
    _outputs.statement(
        "gpio.pwm_write",
        "PWM output",
        "Drives a pin with a PWM duty cycle.",
        (("PIN", PIN), ("DUTY", NUMBER)),
        caps=(Capability.PWM_OUTPUT,),
    ),
    _outputs.statement(
        "gpio.analog_write",
        "analog output",
        "Writes an analog level to a pin.",
        (("PIN", PIN), ("VALUE", NUMBER)),
        caps=(Capability.PWM_OUTPUT,),
    ),
)

TIME = (
    _time.statement(
        "time.delay",
        "delay",
        "Pauses the program for a number of milliseconds.",
        (("MS", NUMBER),),
        implemented_as="delay",
    ),
    _time.value("time.millis", "millis", "Milliseconds since start-up.", NUMBER),
    _time.value("time.micros", "micros", "Microseconds since start-up.", NUMBER),
    _time.expression(
        "time.elapsed",
        "elapsed time",
        "Milliseconds elapsed since an earlier millis timestamp.",
        NUMBER,
        (("SINCE", NUMBER),),
    ),
    _time.statement(
        "time.every",
        "every interval",
        "Runs a body each time an interval has elapsed, without blocking.",
        (("INTERVAL", NUMBER), ("DO", BODY)),
    ),
)

BLOCKS = PROGRAM + INPUTS + OUTPUTS + TIME
