# Master Blockly block catalog (Phase B0.1)

The platform-level list of every programming concept Build Mode may
eventually offer as a block. It is **metadata only**: no Blockly, no C++, no
I/O, and no knowledge of any panel.

## Why it exists

The Build Engine is heading toward

```
Panel Package -> Build Project -> Build Document -> Supported Sections
   -> Available Blocks -> Blockly -> Semantic IR -> Arduino C++
```

"Available Blocks" needs something to be available *from*. The catalog is that
source: a panel package will later **select** blocks from it; it never extends
it. Because block ids are a long-term contract, they are stable and namespaced
(`gpio.digital_write`, `mqtt.publish`, `time.delay`), never tied to wording.

## Blocks are not semantic operations

| | Blockly block | Semantic operation |
|---|---|---|
| What it is | a visual widget (`blockly_type`) | an abstract action (`semantic_operation`) |
| Depends on | Blockly | nothing |
| Example | `digitalwrite` | `gpio.digital_write` |

`semantic_operation` is a stable identifier, **never a C++ fragment**. Several
blocks may share one (`logic.if` and `logic.if_else` are both `logic.if`), so
it is its own field. `blockly_type` and `generator_id` are the catalog's only
contact with Blockly and code generation, and stay empty until a block is built.

## Generic, not Panel 1

Nothing here mentions a panel, scenario or vulnerability. `mqtt.publish` is just
publishing; authorisation and remediation belong to project firmware logic in a
later phase. Pin operands use a dedicated `PIN` value type, and there is no
`smart_home` block — a pin assignment is an ordinary input.

## Future interaction policies

A later phase attaches a policy to each *section* of a build document, not to a
block definition: `READ_ONLY`, `EXPLORE`, `EDIT`, `SECURITY`, `STRUCTURAL`,
`SYSTEM`. The same `gpio.digital_write` may be `EXPLORE` in one section and
`READ_ONLY` in another, which is why the catalog carries no policy. `EXPLORE`
is what will let a student safely change a pin assignment; the `PIN` type is
the hook that makes those inputs identifiable. Nothing enforces any of this yet.

## ESP IDE is a reference, not a source

The categories and concepts follow the [ESP IDE](https://www.espide.eu/esp_ide_v2/index.html?lang=en)
block ecosystem as a *functional* guide. ESP IDE targets MicroPython; this
project targets Arduino C++ for ESP32. No ESP IDE code, generators or wording
were copied and MicroPython is never an internal representation.

## Implementation status

`ImplementationStatus` says how far each block has got. Today (27 categories,
205 blocks):

- **IMPLEMENTED (5)** — `program.setup`, `program.loop`, `gpio.pin_mode`,
  `gpio.digital_write`, `time.delay`. These are the existing Blockly POC blocks
  (`arduino_setup`, `arduino_loop`, `pinmode`, `digitalwrite`, `delay`), kept
  under their original Blockly types. They generate C++ directly from Blockly,
  not through a semantic IR (which does not exist yet).
- **CATALOGED (200)** — catalog entry only. No Blockly block, no generator, not
  in any toolbox.
- `BLOCKLY_DEFINED`, `SEMANTIC_DEFINED`, `GENERATOR_PENDING` — reserved for the
  phases that advance blocks; unused today.

## Frontend

Python is the single source of truth. `scripts/export_blockly_catalog.py`
writes `src/blockly/catalog/masterCatalog.generated.js` (the catalog plus two
toolboxes built by `toolbox.py`); the frontend never restates block metadata.
The live workspace uses `POPULATED_TOOLBOX`: every category that has at least
one usable block. Regenerate after any catalog change:

```
cd backend && python scripts/export_blockly_catalog.py
```

`tests/test_blockly_catalog.py` fails if the checked-in file is stale, and
`npm test` fails if the catalog and `src/blockly/arduinoBlocks.js` disagree on
which Blockly types exist.

## Adding a block

Add it to the right module in `definitions/` (use the category's factory),
regenerate, run both test suites. To make it *usable*, register the Blockly
type and generator in `src/blockly/`, then pass `implemented_as="<type>"`.
