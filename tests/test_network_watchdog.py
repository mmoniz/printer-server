"""
Tests for scripts/network-watchdog.sh.

The script only ever talks to the world through a handful of commands
(ping, ip, systemctl, nmcli, rfkill, dmesg, iw, sleep, sync), so it's tested
by putting fake versions of those on PATH ahead of the real ones and running
the real script against them -- the same "stub the boundary" approach the
CUPS tests use for lp/lpstat.

The script also reads STATE_FILE/LOG_FILE/SYS_CLASS_NET from environment
overrides for exactly this purpose; production runs use the real
/run and /var/log paths.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "network-watchdog.sh"

STUBS = {
    "ping": """\
#!/usr/bin/env bash
echo "ping $*" >> "$CALL_LOG"
exit "${FAKE_PING_EXIT:-0}"
""",
    "ip": """\
#!/usr/bin/env bash
echo "ip $*" >> "$CALL_LOG"
if [[ "$1" == "route" && "${FAKE_NO_ROUTE:-0}" != "1" ]]; then
    echo "default via ${FAKE_GATEWAY:-192.168.1.1} dev wlan0 proto dhcp metric 600"
fi
exit 0
""",
    "systemctl": """\
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALL_LOG"
case "$*" in
    "is-active --quiet NetworkManager") exit "${FAKE_NM_ACTIVE:-1}" ;;
    "is-active --quiet dhcpcd") exit "${FAKE_DHCPCD_ACTIVE:-1}" ;;
    *) exit 0 ;;
esac
""",
    "nmcli": """\
#!/usr/bin/env bash
echo "nmcli $*" >> "$CALL_LOG"
exit 0
""",
    "rfkill": """\
#!/usr/bin/env bash
echo "rfkill $*" >> "$CALL_LOG"
echo stub-output
exit 0
""",
    "dmesg": """\
#!/usr/bin/env bash
echo "dmesg $*" >> "$CALL_LOG"
echo stub-output
exit 0
""",
    "iw": """\
#!/usr/bin/env bash
echo "iw $*" >> "$CALL_LOG"
echo stub-output
exit 0
""",
    "sleep": """\
#!/usr/bin/env bash
exit 0
""",
    "sync": """\
#!/usr/bin/env bash
echo "sync $*" >> "$CALL_LOG"
exit 0
""",
}


@pytest.fixture
def env(tmp_path):
    """A PATH of fake commands, plus the script's env-overridable paths."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    call_log = tmp_path / "calls.log"
    call_log.touch()

    e = dict(os.environ)
    e["PATH"] = f"{bin_dir}{os.pathsep}{e['PATH']}"
    e["CALL_LOG"] = str(call_log)
    e["NETWORK_WATCHDOG_STATE_FILE"] = str(tmp_path / "fails")
    e["NETWORK_WATCHDOG_LOG_FILE"] = str(tmp_path / "network-watchdog.log")
    e["NETWORK_WATCHDOG_SYS_CLASS_NET"] = str(tmp_path / "sys-class-net")
    return e


def run(env):
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result


def calls(env):
    return Path(env["CALL_LOG"]).read_text()


def log_text(env):
    path = Path(env["NETWORK_WATCHDOG_LOG_FILE"])
    return path.read_text() if path.exists() else ""


def state(env):
    path = Path(env["NETWORK_WATCHDOG_STATE_FILE"])
    return path.read_text().strip() if path.exists() else None


def test_reachable_gateway_leaves_no_trail(env):
    env["FAKE_PING_EXIT"] = "0"
    run(env)
    assert state(env) is None
    assert "consecutive failures" not in log_text(env)


def test_recovery_clears_state_and_is_logged(env):
    env["FAKE_PING_EXIT"] = "1"
    run(env)
    run(env)
    assert state(env) == "2"

    env["FAKE_PING_EXIT"] = "0"
    run(env)
    assert state(env) is None
    assert "reachable again after 2 failed check(s)" in log_text(env)


def test_missing_default_route_counts_as_a_failure(env):
    env["FAKE_NO_ROUTE"] = "1"
    run(env)
    assert state(env) == "1"
    assert "no default route yet" in log_text(env)


def test_no_escalation_below_restart_threshold(env):
    env["FAKE_PING_EXIT"] = "1"
    run(env)
    run(env)
    assert state(env) == "2"
    assert "ESCALATION" not in log_text(env)
    assert "nmcli" not in calls(env)
    assert "reboot" not in calls(env)


def test_restarts_via_nmcli_when_networkmanager_is_active(env):
    env["FAKE_PING_EXIT"] = "1"
    env["FAKE_NM_ACTIVE"] = "0"  # systemctl is-active exits 0 => active
    for _ in range(3):
        run(env)

    log = log_text(env)
    assert "ESCALATION: restarting networking (failures=3)" in log
    assert "nmcli networking off" in calls(env)
    assert "nmcli networking on" in calls(env)
    # the diagnostic snapshot actually landed in the log, not just a mention of it
    for label in ("[ip-addr]", "[ip-route]", "[rfkill]", "[dmesg-tail]",
                  "[network-manager-status]"):
        assert label in log


def test_restarts_dhcpcd_when_networkmanager_is_not_active(env):
    env["FAKE_PING_EXIT"] = "1"
    env["FAKE_NM_ACTIVE"] = "1"      # inactive
    env["FAKE_DHCPCD_ACTIVE"] = "0"  # active
    for _ in range(3):
        run(env)
    assert "systemctl restart dhcpcd" in calls(env)
    assert "nmcli" not in calls(env)


def test_cycles_wifi_interfaces_when_no_network_manager_is_active(env):
    env["FAKE_PING_EXIT"] = "1"
    env["FAKE_NM_ACTIVE"] = "1"
    env["FAKE_DHCPCD_ACTIVE"] = "1"
    sys_class_net = Path(env["NETWORK_WATCHDOG_SYS_CLASS_NET"])
    sys_class_net.mkdir(parents=True)
    (sys_class_net / "wlan0").mkdir()

    for _ in range(3):
        run(env)

    assert "ip link set wlan0 down" in calls(env)
    assert "ip link set wlan0 up" in calls(env)


def test_does_not_repeat_the_restart_between_thresholds(env):
    env["FAKE_PING_EXIT"] = "1"
    env["FAKE_NM_ACTIVE"] = "0"
    for _ in range(3):
        run(env)
    run(env)  # failure 4
    run(env)  # failure 5

    assert state(env) == "5"
    assert calls(env).count("nmcli networking off") == 1


def test_reboots_after_the_reboot_threshold(env):
    env["FAKE_PING_EXIT"] = "1"
    env["FAKE_NM_ACTIVE"] = "0"
    for _ in range(6):
        run(env)

    log = log_text(env)
    assert "ESCALATION: network still down after a restart attempt; rebooting (failures=6)" in log
    assert "systemctl reboot" in calls(env)
    assert "sync" in calls(env)
    # state is cleared before reboot, so a boot that never actually happens
    # (this is a stub) doesn't leave a stale counter behind
    assert state(env) is None
