#!/bin/sh
# Cybersecurity Trainer - desktop launcher for the Raspberry Pi touchscreen.
#
# What a tap on the "Cybersecurity Trainer" icon does:
#   1. makes sure the EXISTING cybertrainer-backend.service is running. It is
#      started only when it is not already active; it is never restarted, and
#      no second FastAPI/uvicorn process is ever created here;
#   2. waits until the trainer answers on http://localhost:8000/;
#   3. opens that address in the Pi's installed web browser (the desktop's
#      default browser, else the first of chromium / firefox / epiphany found).
#
# The address is localhost on purpose. The Pi's own browser reaches the backend
# without any network, so this works with only the CyberTrainer access point up
# and no home Wi-Fi or Internet. Laptops on the access point open the Pi's own
# address on that network instead; they do not use this launcher.
#
# Nothing starts at boot: the browser opens only when this script is run (see
# deploy/install-desktop-launcher.sh). Diagnostics go to
# ~/.cache/cybertrainer/launcher.log (replaced on every launch).
#
# Overrides, mainly for troubleshooting and the test suite:
#   CYBERTRAINER_URL           page to wait for and open   (http://localhost:8000/)
#   CYBERTRAINER_SERVICE       systemd unit to check/start (cybertrainer-backend.service)
#   CYBERTRAINER_WAIT_SECONDS  how long to wait            (60)
#   CYBERTRAINER_BROWSER       browser executable to use instead of detecting one

set -u

TITLE='Cybersecurity Trainer'
SERVICE=${CYBERTRAINER_SERVICE:-cybertrainer-backend.service}
URL=${CYBERTRAINER_URL:-http://localhost:8000/}
WAIT_SECONDS=${CYBERTRAINER_WAIT_SECONDS:-60}
SPLASH_PID=

LOG_DIR=${XDG_CACHE_HOME:-$HOME/.cache}/cybertrainer
LOG=$LOG_DIR/launcher.log
if ! { mkdir -p "$LOG_DIR" && : >"$LOG"; } 2>/dev/null; then
  LOG=/dev/null
fi

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" >>"$LOG"; }
has() { command -v "$1" >/dev/null 2>&1; }
have_display() { [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; }

stop_splash() {
  if [ -n "$SPLASH_PID" ]; then
    kill "$SPLASH_PID" 2>/dev/null
    SPLASH_PID=
  fi
}

# Tell the person at the screen (a double-click has no terminal), and the log.
die() {
  stop_splash
  log "ERROR: $1"
  printf '%s: %s\n' "$TITLE" "$1" >&2
  if have_display; then
    if has zenity; then
      zenity --error --no-wrap --title "$TITLE" --text "$1" >/dev/null 2>&1
    elif has xmessage; then
      xmessage -center "$TITLE: $1" >/dev/null 2>&1
    fi
  fi
  exit 1
}

# Succeeds when $1 answers with a 2xx/3xx. --noproxy: this address is local.
responds() {
  if has curl; then
    curl -fsS --noproxy '*' -o /dev/null --max-time 3 "$1" >/dev/null 2>&1
  elif has wget; then
    wget -q -O /dev/null -T 3 -t 1 "$1" >/dev/null 2>&1
  else
    python3 -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3).read(1)' "$1" >/dev/null 2>&1
  fi
}

start_service() {
  log "starting $SERVICE (it was not active)"
  # Prefer passwordless sudo so no password prompt appears in front of a
  # student; without it, plain systemctl asks through the desktop's polkit agent.
  # --no-block: queue the start and return, so the wait below (with its notice)
  # covers the whole start-up instead of this call silently holding the screen.
  if has sudo && sudo -n true >/dev/null 2>&1; then
    sudo -n systemctl start --no-block "$SERVICE" >>"$LOG" 2>&1
  else
    systemctl start --no-block "$SERVICE" >>"$LOG" 2>&1
  fi
}

# The desktop's default browser if it is installed, else the first known one.
find_browser() {
  if [ -n "${CYBERTRAINER_BROWSER:-}" ]; then
    command -v "$CYBERTRAINER_BROWSER" 2>/dev/null
    return
  fi
  default=
  if has xdg-settings; then
    default=$(xdg-settings get default-web-browser 2>/dev/null)
    default=${default%.desktop}
  fi
  for candidate in "$default" chromium chromium-browser firefox-esr firefox epiphany-browser; do
    [ -n "$candidate" ] || continue
    if path=$(command -v "$candidate" 2>/dev/null) && [ -x "$path" ]; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  return 1
}

has curl || has wget || has python3 || die 'curl, wget or python3 is needed to check that the trainer is ready, and none was found.'
has systemctl || die 'systemd (systemctl) was not found, so the trainer service cannot be checked.'

# 1. The service: leave it alone unless it is stopped.
state=$(systemctl is-active "$SERVICE" 2>/dev/null)
log "$SERVICE is ${state:-unknown}"
case $state in
  active | activating | reloading) ;;
  *) start_service || die "The trainer service could not be started. Details: $LOG" ;;
esac

# 2. Wait for the page. A short notice shows only when the wait is not instant.
if ! responds "$URL"; then
  if have_display && has zenity; then
    zenity --info --no-wrap --title "$TITLE" --timeout "$WAIT_SECONDS" \
      --text 'Starting the trainer...\nThe browser opens by itself in a moment.' >/dev/null 2>&1 &
    SPLASH_PID=$!
  fi
  waited=0
  until responds "$URL"; do
    if [ "$waited" -ge "$WAIT_SECONDS" ]; then
      if responds "${URL%/}/health"; then
        die "The trainer is running but its screen is not being served at $URL. Details: $LOG"
      fi
      die "The trainer did not answer at $URL within $WAIT_SECONDS seconds. Check: systemctl status $SERVICE"
    fi
    if [ "$(systemctl is-active "$SERVICE" 2>/dev/null)" = failed ]; then
      die "The trainer service failed to start. Check: systemctl status $SERVICE"
    fi
    sleep 1
    waited=$((waited + 1))
  done
  log "answered at $URL after ${waited}s"
  stop_splash
fi

# 3. The browser.
browser=$(find_browser) || die 'No web browser was found (the desktop default, chromium, firefox and epiphany were tried).'
log "opening $URL with $browser"
case ${browser##*/} in
  chromium* | chrome* | google-chrome*)
    # --password-store=basic: the desktop logs in automatically, so the login
    # keyring is locked, and without this Chromium stops on an "Unlock Keyring"
    # password box before it loads the page. Nothing is stored in this browser.
    set -- --password-store=basic --new-window "$URL"
    ;;
  firefox*) set -- --new-window "$URL" ;;
  *) set -- "$URL" ;;
esac
exec "$browser" "$@" >/dev/null 2>&1
