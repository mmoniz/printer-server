#!/usr/bin/env bash
#
# Install the label print server on a Raspberry Pi running Raspberry Pi OS.
#
#   sudo ./scripts/install.sh
#
# Safe to re-run: every step either creates something or updates it in place.

set -euo pipefail

QUEUE="${QUEUE:-labels}"
APP_USER="${APP_USER:-labelserver}"
APP_DIR="/opt/labelserver"
LIB_DIR="/usr/local/lib/labelserver"
FILTER_DIR="$(cups-config --serverbin 2>/dev/null || echo /usr/lib/cups)/filter"
PPD_DIR="/usr/share/ppd/labelserver"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo"

# --- packages ------------------------------------------------------------
say "Installing packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    cups cups-client cups-filters avahi-daemon \
    python3 python3-venv python3-numpy python3-pil

# --- application user ----------------------------------------------------
say "Creating the $APP_USER user"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
# lpadmin lets the service query and manage the queue.
usermod -aG lpadmin "$APP_USER"

# --- application code ----------------------------------------------------
say "Installing the application to $APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
cp -r "$REPO_DIR/labelserver" "$APP_DIR/"
cp "$REPO_DIR/requirements.txt" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    python3 -m venv --system-site-packages "$APP_DIR/venv"
fi
# --system-site-packages lets us reuse the apt builds of numpy and Pillow,
# which saves a very long compile on a Pi 2.
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# --- CUPS filter ---------------------------------------------------------
say "Installing the CUPS filter into $FILTER_DIR"
[[ -d "$FILTER_DIR" ]] || die "cannot find the CUPS filter directory ($FILTER_DIR)"

# The filter runs as the cups user and imports labelserver from here.
rm -rf "$LIB_DIR"
install -d "$LIB_DIR/labelserver"
install -m 644 "$REPO_DIR/labelserver/__init__.py" "$LIB_DIR/labelserver/"
install -m 644 "$REPO_DIR/labelserver/tspl.py" "$LIB_DIR/labelserver/"

install -m 755 "$REPO_DIR/cups/rastertotspl" "$FILTER_DIR/rastertotspl"

install -d "$PPD_DIR"
install -m 644 "$REPO_DIR/cups/LabelPrinter.ppd" "$PPD_DIR/"

# The filter needs numpy. It runs as the cups user under the system python,
# so numpy must be present system-wide -- that is why python3-numpy is an apt
# dependency above rather than only living in the venv.
python3 -c "import numpy" 2>/dev/null || die "system python is missing numpy"

# --- printer queue -------------------------------------------------------
say "Looking for the printer"
DEVICE_URI="$(lpinfo -v 2>/dev/null | awk '/^direct usb:/ {print $2; exit}')"

if [[ -z "$DEVICE_URI" ]]; then
    warn "no USB printer detected. Plug it in and power it on, then re-run."
    warn "Continuing so the rest of the setup completes."
else
    say "Creating the '$QUEUE' queue for $DEVICE_URI"
    lpadmin -p "$QUEUE" -v "$DEVICE_URI" -P "$PPD_DIR/LabelPrinter.ppd" \
            -o printer-is-shared=true -E
    lpadmin -d "$QUEUE"
    cupsenable "$QUEUE"
    cupsaccept "$QUEUE"
fi

# --- CUPS sharing on the LAN --------------------------------------------
say "Configuring CUPS to serve the local network"
CUPSD=/etc/cups/cupsd.conf
cp -n "$CUPSD" "$CUPSD.orig" || true

if ! grep -q "# labelserver" "$CUPSD"; then
    cat >> "$CUPSD" <<'CONF'

# labelserver: share the queue with the home network
Listen *:631
Browsing On
BrowseLocalProtocols dnssd
CONF
    # Allow the local subnets into the root and printer sections.
    sed -i 's|^  Order allow,deny$|  Order allow,deny\n  Allow @LOCAL|' "$CUPSD"
fi

systemctl enable --now cups
systemctl restart cups

# --- AirPrint advertisement ---------------------------------------------
say "Advertising the queue over AirPrint"
"$REPO_DIR/scripts/airprint.sh" "$QUEUE"

systemctl enable --now avahi-daemon
systemctl restart avahi-daemon

# --- web app service -----------------------------------------------------
say "Installing the web service"
install -m 644 "$REPO_DIR/scripts/labelserver.service" \
        /etc/systemd/system/labelserver.service
sed -i "s/^Environment=LABELSERVER_QUEUE=.*/Environment=LABELSERVER_QUEUE=$QUEUE/" \
        /etc/systemd/system/labelserver.service

systemctl daemon-reload
systemctl enable --now labelserver
systemctl restart labelserver

# --- reliability tweaks --------------------------------------------------
say "Applying reliability tweaks"
install -m 644 "$REPO_DIR/scripts/99-labelprinter.rules" \
        /etc/udev/rules.d/99-labelprinter.rules
udevadm control --reload-rules || true

# A wifi dongle that sleeps takes the whole server off the network with it.
install -m 644 "$REPO_DIR/scripts/wifi-powersave-off.service" \
        /etc/systemd/system/wifi-powersave-off.service
systemctl daemon-reload
systemctl enable --now wifi-powersave-off || warn "could not disable wifi power saving"

# A hung boot (e.g. an SD card fsck stall after unclean power loss) otherwise
# sits dark forever with nobody there to power-cycle it. The Pi's hardware
# watchdog reboots it if systemd itself ever stops petting the watchdog.
say "Enabling the hardware watchdog"
BOOT_CONFIG=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    [[ -f "$candidate" ]] && { BOOT_CONFIG="$candidate"; break; }
done

if [[ -z "$BOOT_CONFIG" ]]; then
    warn "could not find config.txt; skipping hardware watchdog"
else
    grep -q '^dtparam=watchdog=on' "$BOOT_CONFIG" || \
        echo 'dtparam=watchdog=on' >> "$BOOT_CONFIG"

    install -d /etc/systemd/system.conf.d
    cat > /etc/systemd/system.conf.d/watchdog.conf <<'CONF'
[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=10min
CONF
    systemctl daemon-reexec || true
    warn "watchdog needs a reboot to take effect (dtparam is boot-time)"
fi

# --- done ----------------------------------------------------------------
HOSTNAME_SHORT="$(hostname -s)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

say "Done"
cat <<EOF

  Web page   http://$HOSTNAME_SHORT.local/     ${IP:+(or http://$IP/)}
  CUPS admin http://$HOSTNAME_SHORT.local:631/
  Queue      $QUEUE

  AirPrint should now appear on iPhones and iPads on this network.

  Check on things with:
    systemctl status labelserver
    journalctl -u labelserver -f
    lpstat -p $QUEUE

  Print a test label without going through the web page:
    $REPO_DIR/scripts/testprint.sh

EOF
