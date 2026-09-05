#!/bin/bash
# Fix AnyDesk on lab_comp - run: bash ~/fix_anydesk.sh
# For full fix with service restart: sudo bash ~/fix_anydesk.sh

set -euo pipefail

log() { echo "[fix-anydesk] $*"; }

fix_user_tray() {
  log "Restart user tray on DISPLAY=:1"
  pkill -u "$(whoami)" -f '/usr/bin/anydesk' 2>/dev/null || true
  sleep 1
  if [ -S /tmp/.X11-unix/X1 ]; then
    export DISPLAY=:1
    nohup /usr/bin/anydesk --tray >/tmp/anydesk-tray.log 2>&1 &
    sleep 2
  else
    log "WARN: no X session on :1"
  fi
}

show_status() {
  log "Processes:"
  pgrep -af anydesk || true
  if [ -S /tmp/.X11-unix/X1 ]; then
    export DISPLAY=:1
    log "ID: $(anydesk --get-id 2>/dev/null || echo n/a)"
    log "Status: $(anydesk --get-status 2>/dev/null || echo n/a)"
  fi
  log "Relay config:"
  grep -E 'ad.anynet.id=|relay.error|relay.state|last_relay' /etc/anydesk/system.conf || true
  log "Last connections:"
  tail -3 /etc/anydesk/connection_trace.txt 2>/dev/null || true
}

fix_root() {
  log "Full restart (root)"
  killall anydesk 2>/dev/null || true
  sleep 2

  if ! grep -q 'deb.anydesk.com' /etc/apt/sources.list.d/anydesk-stable.list 2>/dev/null; then
    log "Adding AnyDesk apt repo"
    wget -qO - https://keys.anydesk.com/repos/DEB-GPG-KEY | apt-key add -
    echo "deb http://deb.anydesk.com/ all main" > /etc/apt/sources.list.d/anydesk-stable.list
  fi

  log "Updating AnyDesk package"
  apt-get update -qq
  apt-get install -y --only-upgrade anydesk || apt-get install -y anydesk

  systemctl enable anydesk
  systemctl restart anydesk
  sleep 3
}

# kill stuck downloads from earlier diagnostics
pkill -u "$(whoami)" -f 'curl -sI https://download.anydesk.com' 2>/dev/null || true

if [ "${1:-}" = "--root" ] || [ "$(id -u)" -eq 0 ]; then
  fix_root
fi

fix_user_tray
show_status

log "Done. Connect to ID above from your PC."
