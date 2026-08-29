#!/usr/bin/env bash
#
# Runs periodically (see network-watchdog.timer) to catch the case
# wifi-powersave-off.service doesn't: a USB wifi dongle that fails to
# reassociate after a cold boot rather than one that fell asleep. The Pi's
# hardware watchdog (see install.sh) only catches a fully hung system --
# this catches "up, but the network never came back."
#
# Escalates: a few failed checks restart networking; if that doesn't help,
# reboot as a last resort. State lives on tmpfs so it resets every boot.

set -uo pipefail

STATE_FILE="/run/network-watchdog.fails"
RESTART_THRESHOLD=3   # ~9 minutes of failures at the default 3-minute timer
REBOOT_THRESHOLD=6    # ~18 minutes of failures

log() { printf '%s\n' "$*"; }

GATEWAY="$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"
if [[ -z "$GATEWAY" ]]; then
    log "no default route yet; counting as a failure"
else
    if ping -c 2 -W 3 "$GATEWAY" >/dev/null 2>&1; then
        if [[ -f "$STATE_FILE" ]]; then
            log "gateway $GATEWAY reachable again; clearing failure count"
            rm -f "$STATE_FILE"
        fi
        exit 0
    fi
    log "gateway $GATEWAY did not respond"
fi

FAILS=0
[[ -f "$STATE_FILE" ]] && FAILS="$(cat "$STATE_FILE")"
FAILS=$((FAILS + 1))
echo "$FAILS" > "$STATE_FILE"
log "consecutive failures: $FAILS"

if (( FAILS == RESTART_THRESHOLD )); then
    if systemctl is-active --quiet NetworkManager; then
        log "restarting networking via nmcli"
        nmcli networking off && sleep 2 && nmcli networking on
    elif systemctl is-active --quiet dhcpcd; then
        log "restarting dhcpcd"
        systemctl restart dhcpcd
    else
        log "no known network manager active; cycling wl* interfaces directly"
        for dev in /sys/class/net/wl*; do
            [[ -e "$dev" ]] || continue
            iface="$(basename "$dev")"
            ip link set "$iface" down
            sleep 2
            ip link set "$iface" up
        done
    fi
elif (( FAILS >= REBOOT_THRESHOLD )); then
    log "network still down after a restart attempt; rebooting"
    rm -f "$STATE_FILE"
    systemctl reboot
fi
