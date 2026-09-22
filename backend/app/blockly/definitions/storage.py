"""Storage blocks: the on-board flash file system."""

from __future__ import annotations

from app.blockly.definitions.factory import BOOLEAN, LIST, NUMBER, TEXT, CategoryFactory
from app.blockly.models import Capability

_fs = CategoryFactory("filesystem")

_DEPS = ("littlefs",)
_CAPS = (Capability.FILESYSTEM,)

FILESYSTEM = (
    _fs.statement("fs.begin", "file system begin", "Mounts the flash file system.", deps=_DEPS, caps=_CAPS),
    _fs.statement(
        "fs.open", "open file", "Opens a file in a mode such as read, write or append.",
        (("PATH", TEXT), ("MODE", TEXT)), deps=_DEPS, caps=_CAPS,
    ),
    _fs.statement("fs.close", "close file", "Closes the open file.", deps=_DEPS, caps=_CAPS),
    _fs.value("fs.read", "read file", "Reads the open file's contents as text.", TEXT, deps=_DEPS, caps=_CAPS),
    _fs.statement("fs.write", "write file", "Writes text to the open file.", (("DATA", TEXT),), deps=_DEPS, caps=_CAPS),
    _fs.statement("fs.append", "append to file", "Appends text to a file.", (("PATH", TEXT), ("DATA", TEXT)), deps=_DEPS, caps=_CAPS),
    _fs.expression("fs.exists", "file exists", "True when a file exists.", BOOLEAN, (("PATH", TEXT),), deps=_DEPS, caps=_CAPS),
    _fs.statement("fs.delete", "delete file", "Deletes a file.", (("PATH", TEXT),), deps=_DEPS, caps=_CAPS),
    _fs.expression("fs.size", "file size", "A file's size in bytes.", NUMBER, (("PATH", TEXT),), deps=_DEPS, caps=_CAPS),
    _fs.expression("fs.list_files", "list files", "The file names in a directory.", LIST, (("DIRECTORY", TEXT),), deps=_DEPS, caps=_CAPS),
)

BLOCKS = FILESYSTEM
