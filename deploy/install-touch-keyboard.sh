#!/bin/sh
# Set up the touchscreen's on-screen keyboard for the user who runs this script.
# Run it on the Raspberry Pi, from the repository, as the desktop user:
#
#   sh deploy/install-touch-keyboard.sh               # configure
#   sh deploy/install-touch-keyboard.sh --uninstall   # undo (the packages stay)
#
# WHY IT IS A DESKTOP KEYBOARD. A web page cannot start a program or reach
# D-Bus, and the trainer must not fake a keyboard in JavaScript (xterm, Blockly
# and the text fields keep the browser's normal input). So the keyboard belongs
# to the desktop and appears when a text field is tapped. This Pi boots the X11
# desktop (Raspberry Pi Desktop on Openbox, session rpd-x); squeekboard, which
# comes with Raspberry Pi OS, only runs under the Wayland session, so Onboard is
# the keyboard used here.
#
# NEEDED ONCE, WITH INTERNET (the CyberTrainer access point has none), as root:
#   sudo apt install onboard at-spi2-core
# This script installs nothing. at-spi2-core is the accessibility bus: without
# it the browser cannot tell Onboard that a field was tapped, and only the
# floating keyboard icon (below) shows the keyboard.
#
# What it sets, all inside this user's home (the settings Onboard keeps in dconf):
#   - accessibility on (the one question Onboard would otherwise put to the
#     student on its first start: "Enable accessibility now?");
#   - Onboard shows itself when a text field is tapped, and hides when a key is
#     pressed on a physical keyboard;
#   - a small floating keyboard icon that shows or hides it by hand;
#   - it stays above a fullscreen browser, starts hidden, and has no tray icon
#     (the Pi's panel has no tray for it);
#   - ~/.config/autostart/cybertrainer-onboard.desktop starts it at login
#     (Onboard's own autostart entry only applies to Unity and MATE).
# Settings take effect the next time the desktop starts, or when Onboard is
# started by hand: setsid onboard >/dev/null 2>&1 &

set -eu

entry=cybertrainer-onboard.desktop
autostart=${XDG_CONFIG_HOME:-$HOME/.config}/autostart

# schema key value
settings() {
  cat <<'SETTINGS'
org.gnome.desktop.interface toolkit-accessibility true
org.onboard.auto-show enabled true
org.onboard.icon-palette in-use true
org.onboard.window force-to-top true
org.onboard start-minimized true
org.onboard show-status-icon false
SETTINGS
}

# Over ssh or from a menu there may be no D-Bus address in the environment;
# the user's own bus is where the settings service listens.
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$(id -u)/bus" ]; then
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
  export DBUS_SESSION_BUS_ADDRESS
fi

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$autostart/$entry"
  if command -v gsettings >/dev/null 2>&1; then
    settings | while read -r schema key _; do
      gsettings reset "$schema" "$key" 2>/dev/null || true
    done
  fi
  echo "Removed $autostart/$entry and reset the Onboard settings this script set."
  exit 0
fi
if [ "$#" -ne 0 ]; then
  echo "usage: sh deploy/install-touch-keyboard.sh [--uninstall]" >&2
  exit 2
fi

if ! command -v onboard >/dev/null 2>&1; then
  echo "Onboard is not installed. With Internet access, once, as root:" >&2
  echo "  sudo apt install onboard at-spi2-core" >&2
  exit 1
fi
if ! command -v gsettings >/dev/null 2>&1 || ! gsettings list-schemas | grep -qx org.onboard; then
  echo "Onboard's settings (schema org.onboard) are not available; reinstall the onboard-common package." >&2
  exit 1
fi

settings | while read -r schema key value; do
  gsettings set "$schema" "$key" "$value"
done

mkdir -p "$autostart"
cat >"$autostart/$entry.new" <<'ENTRY'
[Desktop Entry]
Type=Application
Name=Onboard (touch keyboard)
Comment=On-screen keyboard for the touchscreen
Exec=onboard --startup-delay=3
NoDisplay=true
ENTRY
mv "$autostart/$entry.new" "$autostart/$entry"

echo "On-screen keyboard configured for $(id -un):"
echo "  autostart : $autostart/$entry"
echo "  behaviour : appears when a text field is tapped; floating icon toggles it; above fullscreen"
if [ ! -x /usr/libexec/at-spi-bus-launcher ]; then
  echo "note: at-spi2-core is not installed, so it cannot appear by itself - only the floating icon works." >&2
  echo "      sudo apt install at-spi2-core" >&2
fi
echo "It starts at the next login; to start it now: setsid onboard >/dev/null 2>&1 &"
