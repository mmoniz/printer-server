#!/usr/bin/env bash
#
# Runs periodically (see network-watchdog.timer) to catch the case
# wifi-powersave-off.service doesn't: a USB wifi dongle that fails to
# reassociate after a cold boot rather than one that fell asleep. The Pi's
# hardware watchdog (see install.sh) only catches a fully hung system --
# this catches "up, but the network never came back."
#
# Escalates: a few failed checks restart networking; if that doesn't help,
# reboot as a last resort. State lives on tmpfs so it resets every boot, but
# every check and every escalation is logged to LOG_FILE on disk, with a
# diagnostic snapshot captured at the moment of each escalation -- that's the
# context that's actually useful once you're staring at this cold days later.

set -uo pipefail

STATE_FILE="${NETWORK_WATCHDOG_STATE_FILE:-/run/network-watchdog.fails}"
LOG_FILE="${NETWORK_WATCHDOG_LOG_FILE:-/var/log/network-watchdog.log}"
SYS_CLASS_NET="${NETWORK_WATCHDOG_SYS_CLASS_NET:-/sys/class/net}"
RESTART_THRESHOLD=3   # ~9 minutes of failures at the default 3-minute timer
REBOOT_THRESHOLD=6    # ~18 minutes of failures

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

# Appends the output of a command under a labelled header, so a snapshot
# reads as a small report rather than a pile of unlabelled text.
dump() {
    local label="$1"; shift
    {
        printf '%s [%s]\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$label"
        "$@" 2>&1 || true
        printf '\n'
    } >> "$LOG_FILE"
}

snapshot() {
    dump "ip-addr" ip -4 addr show
    dump "ip-route" ip route show
    for dev in "$SYS_CLASS_NET"/wl*; do
        [[ -e "$dev" ]] || continue
        dump "iw-link-$(basename "$dev")" iw dev "$(basename "$dev")" link
    done
    dump "rfkill" rfkill list
    dump "dmesg-tail" bash -c "dmesg | tail -n 40"
    dump "network-manager-status" systemctl status NetworkManager --no-pager -l
}

GATEWAY="$(ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"
if [[ -z "$GATEWAY" ]]; then
    log "no default route yet; counting as a failure"
else
    if ping -c 2 -W 3 "$GATEWAY" >/dev/null 2>&1; then
        if [[ -f "$STATE_FILE" ]]; then
            log "gateway $GATEWAY reachable again after $(cat "$STATE_FILE") failed check(s); clearing failure count"
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
    log "ESCALATION: restarting networking (failures=$FAILS) -- snapshot follows"
    snapshot
    if systemctl is-active --quiet NetworkManager; then
        log "restarting networking via nmcli"
        nmcli networking off && sleep 2 && nmcli networking on
    elif systemctl is-active --quiet dhcpcd; then
        log "restarting dhcpcd"
        systemctl restart dhcpcd
    else
        log "no known network manager active; cycling wl* interfaces directly"
        for dev in "$SYS_CLASS_NET"/wl*; do
            [[ -e "$dev" ]] || continue
            iface="$(basename "$dev")"
            ip link set "$iface" down
            sleep 2
            ip link set "$iface" up
        done
    fi
elif (( FAILS >= REBOOT_THRESHOLD )); then
    log "ESCALATION: network still down after a restart attempt; rebooting (failures=$FAILS) -- snapshot follows"
    snapshot
    rm -f "$STATE_FILE"
    sync
    systemctl reboot
fi
