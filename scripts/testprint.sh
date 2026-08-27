#!/usr/bin/env bash
#
# Print a test label, bypassing as much of the stack as you ask it to.
#
#   ./scripts/testprint.sh              # through CUPS, the normal path
#   ./scripts/testprint.sh --raw        # straight to the USB device
#
# --raw is the one to reach for when nothing prints and you want to know
# whether the problem is the printer or everything above it. It writes TSPL
# directly to /dev/usb/lp0 with no CUPS, no filter and no queue involved.

set -euo pipefail

QUEUE="${QUEUE:-labels}"
DEVICE="${DEVICE:-/dev/usb/lp0}"
MODE="cups"

[[ "${1:-}" == "--raw" ]] && MODE="raw"

# A minimal 4x6 label: border, crosshairs at the corners and a centre line, so
# alignment problems are obvious at a glance.
tspl() {
cat <<'EOF'
SIZE 102 mm ,153 mm
REFERENCE 0,0
DIRECTION 0,0
GAP 3 mm,0 mm
OFFSET 0 mm
DENSITY 6
SPEED 4
CLS
BOX 8,8,804,1210,4
TEXT 60,60,"4",0,1,1,"LABEL PRINTER OK"
TEXT 60,120,"3",0,1,1,"4x6 in / 102x153 mm"
TEXT 60,170,"3",0,1,1,"203 dpi / 812x1218 dots"
BOX 8,8,108,108,3
BOX 704,8,804,108,3
BOX 8,1110,108,1210,3
BOX 704,1110,804,1210,3
BAR 8,609,796,4
TEXT 60,650,"2",0,1,1,"If the border is cut off, adjust REFERENCE."
TEXT 60,690,"2",0,1,1,"If the label creeps, check GAP and media type."
PRINT 1,1
EOF
}

if [[ "$MODE" == "raw" ]]; then
    [[ -w "$DEVICE" ]] || {
        echo "cannot write to $DEVICE" >&2
        echo "is the printer plugged in and on? try: ls -l /dev/usb/" >&2
        exit 1
    }
    echo "Writing TSPL straight to $DEVICE ..."
    tspl > "$DEVICE"
    echo "Sent. If nothing came out, the problem is the printer or the cable."
else
    command -v lp >/dev/null || { echo "lp not found; install cups-client" >&2; exit 1; }
    echo "Sending through the '$QUEUE' queue ..."
    # -oraw skips the filter chain: these are already printer commands.
    tspl | lp -d "$QUEUE" -o raw -t "test-label"
    echo
    echo "Queued. Watch it with: lpstat -o $QUEUE"
    echo "If it sticks in the queue, check: journalctl -u cups -n 50"
fi
