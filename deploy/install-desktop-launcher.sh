#!/bin/sh
# Install (or remove) the "Cybersecurity Trainer" desktop launcher for the user
# who runs this script. Run it on the Raspberry Pi, from the repository:
#
#   sh deploy/install-desktop-launcher.sh              # install / refresh
#   sh deploy/install-desktop-launcher.sh --uninstall  # remove
#
# Everything it touches is inside the user's home: the desktop icon and the
# applications-menu entry (both generated from deploy/cybertrainer.desktop.in
# with this checkout's absolute paths) and one file-manager preference, quick_exec
# in ~/.config/libfm/libfm.conf (explained below; --uninstall leaves it as it is).
# It needs no sudo, installs no package, touches no system file and does not
# change cybertrainer-backend.service. Safe to run again.

set -eu

here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
launcher=$here/cybertrainer-launcher.sh
template=$here/cybertrainer.desktop.in
icon=$repo/public/favicon.svg
entry=cybertrainer.desktop

applications=${XDG_DATA_HOME:-$HOME/.local/share}/applications
desktop=$(xdg-user-dir DESKTOP 2>/dev/null || true)
if [ -z "$desktop" ] || [ "$desktop" = "$HOME" ]; then
  desktop=$HOME/Desktop
fi

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$desktop/$entry" "$applications/$entry"
  echo "Removed $desktop/$entry and $applications/$entry"
  exit 0
fi
if [ "$#" -ne 0 ]; then
  echo "usage: sh deploy/install-desktop-launcher.sh [--uninstall]" >&2
  exit 2
fi

for needed in "$launcher" "$template" "$icon"; do
  [ -f "$needed" ] || { echo "missing $needed - run this from a complete checkout" >&2; exit 1; }
done
# The paths are substituted with sed below, so keep its special characters out.
case $launcher$icon in
  *'|'* | *'&'* | *'\'*) echo "the checkout path may not contain | & or \\" >&2; exit 1 ;;
esac

mkdir -p "$applications" "$desktop"
for target in "$applications/$entry" "$desktop/$entry"; do
  sed -e "s|@LAUNCHER@|$launcher|" -e "s|@ICON@|$icon|" "$template" >"$target.new"
  mv "$target.new" "$target"
done

chmod +x "$desktop/$entry"
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$desktop/$entry" || echo "warning: desktop-file-validate reported the issues above" >&2
fi

# Raspberry Pi OS's file manager (pcmanfm) asks "Execute / Execute in Terminal /
# Open / Cancel" before running ANY launcher on the desktop, unless its "Don't ask
# options on launch executable file" preference is on (libfm key quick_exec).
# Turn that one preference on for this user so a tap just starts the trainer.
# libfm reads ~/.config/libfm/libfm.conf (not XDG_CONFIG_HOME) and only at start-up.
libfm=$HOME/.config/libfm/libfm.conf
quick_exec=skipped
if command -v pcmanfm >/dev/null 2>&1; then
  mkdir -p "$(dirname "$libfm")"
  if [ ! -f "$libfm" ]; then
    printf '[config]\nquick_exec=1\n' >"$libfm"
  elif grep -q '^quick_exec=' "$libfm"; then
    sed -i 's/^quick_exec=.*/quick_exec=1/' "$libfm"
  elif grep -q '^\[config\]' "$libfm"; then
    sed -i '/^\[config\]/a quick_exec=1' "$libfm"
  else
    printf '\n[config]\nquick_exec=1\n' >>"$libfm"
  fi
  quick_exec=on
fi

echo "Installed the Cybersecurity Trainer launcher:"
echo "  desktop icon : $desktop/$entry"
echo "  menu entry   : $applications/$entry"
echo "It opens http://localhost:8000/ and starts cybertrainer-backend.service only if it is stopped."
if [ "$quick_exec" = on ]; then
  echo "File manager : 'Don't ask options on launch executable file' is now on ($libfm)."
  echo "               It applies the next time the desktop starts (log out and in, or reboot);"
  echo "               until then the first tap shows the standard Execute File question - choose Execute."
fi
