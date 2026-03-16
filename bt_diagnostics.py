#!/usr/bin/env python3
"""
Bluetooth Diagnostics TUI - part of System Doctor.
15 bluetooth checks with two-panel curses interface and contextual fix actions.
Covers Ubuntu and Linux Mint (18.x / 19.x / 20.x / 21.x / 22.x).
"""

import os
import re
import shutil
import subprocess
import curses
import datetime
from pathlib import Path


# ─── Subprocess helpers ───────────────────────────────────────────────────────

def _run(args, timeout=15):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = r.stdout + (r.stderr if r.returncode != 0 else '')
        return r.returncode, out.splitlines()
    except FileNotFoundError:
        return -1, []
    except subprocess.TimeoutExpired:
        return -1, [f'(timeout after {timeout}s)']
    except Exception as e:
        return -1, [f'(error: {e})']


def _available(cmd):
    return shutil.which(cmd) is not None


def _read_file(path):
    """Return lines of a file, or [] on error."""
    try:
        return Path(path).read_text().splitlines()
    except OSError:
        return []


def _service_active(name):
    rc, _ = _run(['systemctl', 'is-active', '--quiet', name])
    return rc == 0


def _detect_bt_iface():
    """Return first hciX interface name, or None."""
    _, lines = _run(['hciconfig', '-a'])
    for line in lines:
        m = re.match(r'^(hci\d+):', line)
        if m:
            return m.group(1)
    return None


# ─── Fix ─────────────────────────────────────────────────────────────────────

class Fix:
    """A runnable remediation action tied to a diagnostic result."""

    def __init__(self, desc, cmd, needs_root=False, recheck=True):
        self.desc = desc
        self.cmd = cmd
        self.needs_root = needs_root
        self.recheck = recheck

    def cmd_display(self):
        if callable(self.cmd):
            return '(built-in action)'
        return ' '.join(str(a) for a in self.cmd)

    def run(self, timeout=30):
        """Returns (success: bool, output: str)."""
        if callable(self.cmd):
            return self.cmd()
        rc, lines = _run(self.cmd, timeout=timeout)
        return rc == 0, '\n'.join(lines)


# ─── Check result ─────────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self):
        self.status = 'notrun'
        self.brief = 'Not run'
        self.lines = []
        self.issues = []   # (severity, message)
        self.fixes = []    # Fix objects

    def section(self, title):
        self.lines.append('')
        self.lines.append(f'  ── {title} ──')

    def append(self, *lines):
        for line in lines:
            if isinstance(line, list):
                self.lines.extend(line)
            else:
                self.lines.append(str(line))

    def ok(self, msg):
        self.issues.append(('ok', msg))
        self.lines.append(f'  ✓ {msg}')

    def warning(self, msg):
        self.issues.append(('warning', msg))
        self.lines.append(f'  ⚠ {msg}')

    def critical(self, msg):
        self.issues.append(('critical', msg))
        self.lines.append(f'  ✗ {msg}')

    def info(self, msg):
        self.lines.append(f'  ℹ {msg}')

    def add_fix(self, fix):
        self.fixes.append(fix)

    def cmd(self, args, label=None, timeout=15):
        if label:
            self.section(label)
        rc, lines = _run(args, timeout=timeout)
        if not lines and rc == -1:
            self.lines.append(f'  (command not available: {args[0]})')
        else:
            self.lines.extend(f'  {l}' for l in lines)
        return rc, lines

    def finalize(self):
        n_crit = sum(1 for s, _ in self.issues if s == 'critical')
        n_warn = sum(1 for s, _ in self.issues if s == 'warning')
        if n_crit:
            self.status = 'critical'
            self.brief = f'{n_crit} critical' + (f', {n_warn} warn' if n_warn else '')
        elif n_warn:
            self.status = 'warning'
            self.brief = f'{n_warn} warning(s)'
        else:
            self.status = 'ok'
            self.brief = 'OK'
        return self


# ─── Diagnostic checks ────────────────────────────────────────────────────────

def check_system_info():
    r = CheckResult()
    r.cmd(['uname', '-a'], 'Kernel')
    r.section('OS Release')
    lines = _read_file('/etc/os-release')
    r.lines.extend(f'  {l}' for l in lines[:14])
    if not lines:
        r.lines.append('  /etc/os-release not found')
    r.cmd(['uptime'], 'Uptime')
    r.finalize()
    return r


def check_bt_hardware():
    r = CheckResult()

    r.section('PCI Bluetooth Adapters')
    _, lines = _run(['lspci'])
    found = [l for l in lines if re.search(r'bluetooth|wireless', l, re.I)]
    r.lines.extend(f'  {l}' for l in found) if found else r.lines.append('  (none via PCI)')

    r.section('USB Bluetooth Adapters')
    _, lines = _run(['lsusb'])
    found = [l for l in lines if re.search(r'bluetooth', l, re.I)]
    if found:
        r.lines.extend(f'  {l}' for l in found)
        r.ok(f'USB Bluetooth device(s) found: {len(found)}')
    else:
        r.lines.append('  (none via USB)')

    r.section('hciconfig — HCI interfaces')
    rc, lines = _run(['hciconfig', '-a'])
    if rc == -1:
        r.critical('hciconfig not found — bluez-tools may not be installed')
        r.add_fix(Fix('Install bluez and tools',
                      ['sudo', 'apt-get', 'install', '-y', 'bluez', 'bluez-tools'],
                      needs_root=True, recheck=True))
    elif not lines:
        r.critical('No HCI (Bluetooth) interfaces found')
        r.add_fix(Fix('Load btusb module',
                      ['sudo', 'modprobe', 'btusb'],
                      needs_root=True, recheck=True))
    else:
        r.lines.extend(f'  {l}' for l in lines)
        iface_names = [re.match(r'^(hci\d+):', l).group(1)
                       for l in lines if re.match(r'^(hci\d+):', l)]
        r.ok(f'HCI interface(s) found: {", ".join(iface_names)}')

    if _available('lshw'):
        r.cmd(['lshw', '-C', 'communication', '-short'], 'lshw Communication')

    r.finalize()
    return r


def check_bt_kernel():
    r = CheckResult()

    r.section('Loaded Bluetooth Modules')
    _, lines = _run(['lsmod'])
    bt_mods = [l for l in lines if re.search(r'^(bluetooth|btusb|btintel|btbcm|btrtl|btrsi|ath3k|rfcomm|bnep|hidp|hci_uart)', l)]
    if bt_mods:
        r.lines.extend(f'  {l}' for l in bt_mods)
        mod_names = [l.split()[0] for l in bt_mods]
        r.ok(f'Bluetooth modules loaded: {", ".join(mod_names[:6])}')
    else:
        r.critical('No Bluetooth kernel modules loaded')
        r.add_fix(Fix('Load bluetooth + btusb modules',
                      ['sudo', 'modprobe', 'bluetooth'],
                      needs_root=True, recheck=False))
        r.add_fix(Fix('Load btusb (USB adapters)',
                      ['sudo', 'modprobe', 'btusb'],
                      needs_root=True, recheck=True))

    r.section('dmesg — Bluetooth Messages')
    _, lines = _run(['dmesg', '--level=err,warn', '--facility=kern'], timeout=10)
    bt_lines = [l for l in lines if re.search(r'bluetooth|btusb|hci\d', l, re.I)]
    if bt_lines:
        r.lines.extend(f'  {l}' for l in bt_lines[-20:])
        r.warning(f'{len(bt_lines)} Bluetooth kernel message(s) found in dmesg')
    else:
        _, lines = _run(['dmesg'], timeout=10)
        bt_lines = [l for l in lines if re.search(r'bluetooth|btusb|hci\d', l, re.I)]
        r.lines.extend(f'  {l}' for l in bt_lines[-20:])
        if not bt_lines:
            r.info('No Bluetooth messages in dmesg')

    r.finalize()
    return r


def check_bt_service():
    r = CheckResult()

    r.section('bluetoothd Service (bluetooth.service)')
    rc, lines = _run(['systemctl', 'status', 'bluetooth.service'])
    r.lines.extend(f'  {l}' for l in lines[:25])

    if rc == 0 and _service_active('bluetooth.service'):
        r.ok('bluetooth.service is active and running')
    else:
        r.critical('bluetooth.service is NOT running')
        r.add_fix(Fix('Start bluetooth.service',
                      ['sudo', 'systemctl', 'start', 'bluetooth.service'],
                      needs_root=True, recheck=True))
        r.add_fix(Fix('Enable bluetooth.service at boot',
                      ['sudo', 'systemctl', 'enable', 'bluetooth.service'],
                      needs_root=True, recheck=False))

    r.section('Service enabled at boot')
    rc_en, _ = _run(['systemctl', 'is-enabled', 'bluetooth.service'])
    if rc_en == 0:
        r.ok('bluetooth.service is enabled at boot')
    else:
        r.warning('bluetooth.service is NOT enabled at boot — will not start after reboot')
        r.add_fix(Fix('Enable bluetooth.service at boot',
                      ['sudo', 'systemctl', 'enable', 'bluetooth.service'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_bt_interface():
    r = CheckResult()
    iface = _detect_bt_iface()

    r.section('HCI Interface Details')
    if not iface:
        r.critical('No HCI interface detected (is Bluetooth hardware present?)')
        r.add_fix(Fix('Bring up hci0 interface',
                      ['sudo', 'hciconfig', 'hci0', 'up'],
                      needs_root=True, recheck=True))
        r.finalize()
        return r

    r.info(f'Primary interface: {iface}')
    rc, lines = _run(['hciconfig', '-a', iface])
    r.lines.extend(f'  {l}' for l in lines)

    # Check if UP
    iface_up = any('UP' in l for l in lines)
    if iface_up:
        r.ok(f'{iface} is UP')
    else:
        r.critical(f'{iface} is DOWN')
        r.add_fix(Fix(f'Bring up {iface}',
                      ['sudo', 'hciconfig', iface, 'up'],
                      needs_root=True, recheck=True))

    # Check if PSCAN/ISCAN (discoverable/connectable)
    pscan = any('PSCAN' in l for l in lines)
    iscan = any('ISCAN' in l for l in lines)
    if pscan:
        r.ok(f'{iface} is connectable (PSCAN)')
    else:
        r.info(f'{iface} is not connectable (PSCAN off) — normal when not pairing')

    if iscan:
        r.info(f'{iface} is discoverable (ISCAN on)')

    r.section('btmgmt info')
    if _available('btmgmt'):
        rc, lines = _run(['btmgmt', 'info'])
        r.lines.extend(f'  {l}' for l in lines)
    else:
        r.info('btmgmt not available')

    r.finalize()
    return r


def check_rfkill():
    r = CheckResult()
    r.section('rfkill — Radio Kill Switches')

    if not _available('rfkill'):
        r.warning('rfkill not installed')
        r.add_fix(Fix('Install rfkill',
                      ['sudo', 'apt-get', 'install', '-y', 'rfkill'],
                      needs_root=True, recheck=True))
        r.finalize()
        return r

    rc, lines = _run(['rfkill', 'list'])
    r.lines.extend(f'  {l}' for l in lines)

    bt_blocks = []
    in_bt = False
    for line in lines:
        if re.search(r'bluetooth|hci', line, re.I):
            in_bt = True
        if in_bt and 'Soft blocked: yes' in line:
            bt_blocks.append('soft')
        if in_bt and 'Hard blocked: yes' in line:
            bt_blocks.append('hard')
        if line.strip() == '':
            in_bt = False

    if 'hard' in bt_blocks:
        r.critical('Bluetooth is HARD blocked (physical switch or BIOS setting)')
        r.info('Check your laptop hardware switch or BIOS/UEFI Bluetooth setting')
    elif 'soft' in bt_blocks:
        r.warning('Bluetooth is SOFT blocked (software kill switch)')
        r.add_fix(Fix('Unblock Bluetooth via rfkill',
                      ['sudo', 'rfkill', 'unblock', 'bluetooth'],
                      needs_root=True, recheck=True))
    else:
        r.ok('Bluetooth is not blocked by rfkill')

    r.finalize()
    return r


def check_paired_devices():
    r = CheckResult()

    r.section('Paired/Trusted Devices (bluetoothctl)')
    if not _available('bluetoothctl'):
        r.critical('bluetoothctl not found — install bluez')
        r.add_fix(Fix('Install bluez',
                      ['sudo', 'apt-get', 'install', '-y', 'bluez'],
                      needs_root=True, recheck=True))
        r.finalize()
        return r

    rc, lines = _run(['bluetoothctl', 'devices'], timeout=10)
    if rc != 0 or not lines:
        r.info('No paired devices found')
    else:
        r.lines.extend(f'  {l}' for l in lines)
        r.ok(f'{len(lines)} paired device(s) found')

    r.section('Connected Devices')
    rc, lines = _run(['bluetoothctl', 'devices', 'Connected'], timeout=10)
    if rc == 0 and lines:
        r.lines.extend(f'  {l}' for l in lines)
        r.ok(f'{len(lines)} device(s) currently connected')
    else:
        r.info('No devices currently connected')

    r.section('Trusted Devices')
    rc, lines = _run(['bluetoothctl', 'devices', 'Trusted'], timeout=10)
    if rc == 0 and lines:
        r.lines.extend(f'  {l}' for l in lines)
        r.info(f'{len(lines)} trusted device(s)')
    else:
        r.info('No trusted devices')

    r.finalize()
    return r


def check_scan():
    r = CheckResult()
    r.section('Nearby Devices Scan (5 second scan)')
    r.info('Scanning for nearby Bluetooth devices — this may take a few seconds...')

    if not _available('bluetoothctl'):
        r.critical('bluetoothctl not found')
        r.finalize()
        return r

    # Use timeout to limit scan duration
    rc, lines = _run(['timeout', '7', 'bluetoothctl', 'scan', 'on'], timeout=10)
    # bluetoothctl scan on never cleanly exits — any output is informational
    r.lines.extend(f'  {l}' for l in lines[:30])

    r.section('Devices Found After Scan')
    rc2, dev_lines = _run(['bluetoothctl', 'devices'], timeout=8)
    r.lines.extend(f'  {l}' for l in dev_lines)
    if dev_lines:
        r.ok(f'{len(dev_lines)} device(s) visible')
    else:
        r.info('No devices visible (check remote device is discoverable)')

    r.finalize()
    return r


def check_audio_bt():
    r = CheckResult()

    r.section('PulseAudio Bluetooth Modules')
    if _available('pactl'):
        rc, lines = _run(['pactl', 'list', 'modules', 'short'])
        bt_mods = [l for l in lines if re.search(r'bluetooth|a2dp|hsp|hfp', l, re.I)]
        r.lines.extend(f'  {l}' for l in bt_mods) if bt_mods else r.lines.append('  (no Bluetooth audio modules loaded)')
        if bt_mods:
            r.ok(f'Bluetooth audio modules loaded: {len(bt_mods)}')
        else:
            r.warning('No Bluetooth audio modules in PulseAudio')
            r.add_fix(Fix('Install PulseAudio Bluetooth plugin',
                          ['sudo', 'apt-get', 'install', '-y', 'pulseaudio-module-bluetooth'],
                          needs_root=True, recheck=True))
            r.add_fix(Fix('Load module-bluetooth-discover in PulseAudio',
                          ['pactl', 'load-module', 'module-bluetooth-discover'],
                          needs_root=False, recheck=True))

        r.section('PulseAudio Sinks (Audio Outputs)')
        rc, lines = _run(['pactl', 'list', 'sinks', 'short'])
        bt_sinks = [l for l in lines if re.search(r'bluetooth|a2dp|hsp', l, re.I)]
        r.lines.extend(f'  {l}' for l in lines)
        if bt_sinks:
            r.ok(f'Bluetooth audio sinks available: {len(bt_sinks)}')
    else:
        r.info('pactl not available (PulseAudio not installed)')

    r.section('PipeWire + WirePlumber Bluetooth')
    if _available('pw-cli'):
        rc, lines = _run(['pw-cli', 'list-objects', 'Node'], timeout=10)
        bt_nodes = [l for l in lines if re.search(r'bluetooth|a2dp|hsp|hfp', l, re.I)]
        if bt_nodes:
            r.lines.extend(f'  {l}' for l in bt_nodes)
            r.ok(f'PipeWire Bluetooth nodes: {len(bt_nodes)}')
        else:
            r.info('No PipeWire Bluetooth nodes found (device may not be connected)')
    else:
        r.info('PipeWire (pw-cli) not installed — using PulseAudio')

    r.finalize()
    return r


def check_obex():
    r = CheckResult()

    r.section('OBEX File Transfer (obexd)')
    rc, lines = _run(['systemctl', 'status', 'obex.service', '--no-pager'])
    r.lines.extend(f'  {l}' for l in lines[:15])
    if _service_active('obex.service'):
        r.ok('obex.service is running (file transfer available)')
    else:
        r.info('obex.service not running (file transfer inactive)')
        r.add_fix(Fix('Start obex.service for file transfer',
                      ['systemctl', '--user', 'start', 'obex'],
                      needs_root=False, recheck=True))

    r.section('obexftp / obexctl')
    if _available('obexctl'):
        r.ok('obexctl is available')
    else:
        r.info('obexctl not installed (optional for file transfer)')
        r.add_fix(Fix('Install obexftp tools',
                      ['sudo', 'apt-get', 'install', '-y', 'obexftp'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_input_devices():
    r = CheckResult()

    r.section('Bluetooth Input Devices (HID)')
    _, lines = _run(['ls', '-la', '/dev/input/'])
    r.lines.extend(f'  {l}' for l in lines)

    r.section('Connected HID Devices via bluetoothctl')
    rc, lines = _run(['bluetoothctl', 'devices', 'Connected'], timeout=8)
    if rc == 0 and lines:
        r.lines.extend(f'  {l}' for l in lines)
        r.ok(f'{len(lines)} input/HID device(s) connected')
    else:
        r.info('No Bluetooth HID devices currently connected')

    r.section('Loaded HID Modules')
    _, lsmod_lines = _run(['lsmod'])
    hid_mods = [l for l in lsmod_lines if re.search(r'^(hidp|bluetooth_6lowpan|hid_generic)', l)]
    r.lines.extend(f'  {l}' for l in hid_mods) if hid_mods else r.lines.append('  (no BT HID modules detected)')

    r.section('udev Rules for Bluetooth')
    for rule_path in ['/etc/udev/rules.d/', '/lib/udev/rules.d/']:
        _, files = _run(['find', rule_path, '-name', '*bluetooth*', '-o', '-name', '*hci*'], timeout=5)
        for f in files:
            r.lines.append(f'  {f}')

    r.finalize()
    return r


def check_power_management():
    r = CheckResult()

    r.section('Bluetooth Power Management (TLP/autosuspend)')

    # Check TLP
    if _available('tlp-stat'):
        rc, lines = _run(['tlp-stat', '-b'], timeout=10)
        bt_lines = [l for l in lines if re.search(r'bluetooth|bt', l, re.I)]
        r.lines.extend(f'  {l}' for l in bt_lines) if bt_lines else r.lines.append('  (TLP: no explicit BT settings found)')

    # Check USB autosuspend for BT adapters
    r.section('USB Autosuspend for Bluetooth Adapters')
    _, lsusb = _run(['lsusb'])
    bt_usb = [l for l in lsusb if re.search(r'bluetooth', l, re.I)]
    for line in bt_usb:
        m = re.search(r'Bus (\d+) Device (\d+)', line)
        if m:
            bus, dev = m.group(1), m.group(2)
            sus_path = f'/sys/bus/usb/devices/{bus}-{dev}/power/autosuspend_delay_ms'
            sus_lines = _read_file(sus_path)
            if sus_lines:
                delay = sus_lines[0].strip()
                r.lines.append(f'  {line.strip()}: autosuspend_delay_ms={delay}')
                if delay not in ('0', '-1'):
                    r.warning(f'USB autosuspend active (delay={delay}ms) — may cause BT drops')
                    r.add_fix(Fix('Disable USB autosuspend for this BT adapter (session)',
                                  ['sudo', 'sh', '-c',
                                   f'echo -1 > /sys/bus/usb/devices/{bus}-{dev}/power/autosuspend_delay_ms'],
                                  needs_root=True, recheck=True))

    r.section('bluetoothd Power Settings')
    # Check /etc/bluetooth/main.conf for AutoEnable
    conf_lines = _read_file('/etc/bluetooth/main.conf')
    if conf_lines:
        r.lines.extend(f'  {l}' for l in conf_lines if l.strip() and not l.strip().startswith('#'))
        auto = [l for l in conf_lines if re.match(r'\s*AutoEnable\s*=', l)]
        if auto:
            val = auto[0].split('=')[-1].strip().lower()
            if val == 'true':
                r.ok('AutoEnable=true — Bluetooth powers on at boot')
            else:
                r.info('AutoEnable=false — Bluetooth may not start automatically')
                r.add_fix(Fix('Set AutoEnable=true in /etc/bluetooth/main.conf',
                              lambda: _patch_auto_enable(),
                              needs_root=True, recheck=True))
        else:
            r.info('AutoEnable not set in /etc/bluetooth/main.conf (defaults to false on some distros)')
    else:
        r.info('/etc/bluetooth/main.conf not found')

    r.finalize()
    return r


def _patch_auto_enable():
    """Enable AutoEnable=true in /etc/bluetooth/main.conf."""
    conf_path = Path('/etc/bluetooth/main.conf')
    try:
        text = conf_path.read_text()
        if re.search(r'^AutoEnable\s*=', text, re.MULTILINE):
            text = re.sub(r'^AutoEnable\s*=.*', 'AutoEnable=true', text, flags=re.MULTILINE)
        elif '[Policy]' in text:
            text = text.replace('[Policy]', '[Policy]\nAutoEnable=true')
        else:
            text += '\n[Policy]\nAutoEnable=true\n'
        conf_path.write_text(text)
        return True, 'AutoEnable=true written to /etc/bluetooth/main.conf'
    except Exception as e:
        return False, str(e)


def check_system_logs():
    r = CheckResult()

    r.section('journalctl — bluetooth.service (last 50 lines)')
    rc, lines = _run(['journalctl', '-u', 'bluetooth.service', '-n', '50',
                      '--no-pager', '--output=short-iso'], timeout=15)
    r.lines.extend(f'  {l}' for l in lines)

    errors = [l for l in lines if re.search(r'error|fail|refused|timeout', l, re.I)]
    if errors:
        r.warning(f'{len(errors)} error/failure message(s) in bluetooth service log')

    r.section('dmesg — Bluetooth (last 30 messages)')
    _, dm_lines = _run(['dmesg', '-T'], timeout=10)
    bt_dm = [l for l in dm_lines if re.search(r'bluetooth|btusb|hci\d', l, re.I)]
    r.lines.extend(f'  {l}' for l in bt_dm[-30:])
    if not bt_dm:
        r.info('No recent Bluetooth messages in dmesg')

    r.section('journalctl — obex.service')
    _, ob_lines = _run(['journalctl', '-u', 'obex.service', '-n', '20',
                        '--no-pager', '--output=short-iso'], timeout=10)
    r.lines.extend(f'  {l}' for l in ob_lines)

    r.finalize()
    return r


def check_config():
    r = CheckResult()

    r.section('/etc/bluetooth/main.conf')
    conf_lines = _read_file('/etc/bluetooth/main.conf')
    if conf_lines:
        r.lines.extend(f'  {l}' for l in conf_lines)
        r.ok('main.conf found')
    else:
        r.warning('/etc/bluetooth/main.conf not found — using defaults')
        r.add_fix(Fix('Reinstall bluez to restore default config',
                      ['sudo', 'apt-get', 'install', '--reinstall', '-y', 'bluez'],
                      needs_root=True, recheck=True))

    r.section('/var/lib/bluetooth — Paired Device Databases')
    bt_db = Path('/var/lib/bluetooth')
    if bt_db.exists():
        rc, lines = _run(['find', str(bt_db), '-maxdepth', '3', '-name', 'info'])
        r.lines.extend(f'  {l}' for l in lines)
        r.info(f'{len(lines)} paired device record(s) stored')
    else:
        r.info('/var/lib/bluetooth not found (no devices paired)')

    r.section('Installed Bluetooth Packages')
    rc, lines = _run(['dpkg', '-l', 'bluez*', 'bluetooth*', 'pulseaudio-module-bluetooth*',
                      'python3-dbus*', 'libbluetooth*'], timeout=10)
    inst = [l for l in lines if l.startswith('ii')]
    r.lines.extend(f'  {l}' for l in inst)
    if inst:
        r.ok(f'{len(inst)} Bluetooth-related package(s) installed')
    else:
        r.critical('No Bluetooth packages found — bluez may not be installed')
        r.add_fix(Fix('Install bluez core packages',
                      ['sudo', 'apt-get', 'install', '-y', 'bluez', 'bluez-tools',
                       'pulseaudio-module-bluetooth'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_advanced():
    r = CheckResult()

    r.section('bluetoothctl — Controller Info')
    if _available('bluetoothctl'):
        rc, lines = _run(['bluetoothctl', 'show'], timeout=10)
        r.lines.extend(f'  {l}' for l in lines)
        powered = any('Powered: yes' in l for l in lines)
        disc = any('Discoverable: yes' in l for l in lines)
        pair = any('Pairable: yes' in l for l in lines)
        if powered:
            r.ok('Controller is powered on')
        else:
            r.critical('Controller is powered OFF')
            r.add_fix(Fix('Power on Bluetooth controller',
                          ['bluetoothctl', 'power', 'on'],
                          needs_root=False, recheck=True))
        if not pair:
            r.warning('Controller not pairable (cannot accept new device pairings)')
            r.add_fix(Fix('Enable pairable mode',
                          ['bluetoothctl', 'pairable', 'on'],
                          needs_root=False, recheck=True))
    else:
        r.critical('bluetoothctl not available')

    r.section('Adapter Capabilities (hciconfig features)')
    iface = _detect_bt_iface()
    if iface:
        r.cmd(['hciconfig', iface, 'features'], label=None)
        r.cmd(['hciconfig', iface, 'version'], label=None)

    r.section('L2CAP Socket Test')
    if _available('l2ping') and iface:
        r.info('l2ping available (use manually: l2ping -c 3 <MAC>')
    else:
        r.info('l2ping not available or no HCI interface')

    r.section('Bluetooth GATT / LE Scan')
    if _available('hcitool'):
        rc, lines = _run(['hcitool', 'lescan', '--passive'], timeout=8)
        le_lines = [l for l in lines if re.search(r'[0-9A-F]{2}:[0-9A-F]{2}', l, re.I)]
        r.lines.extend(f'  {l}' for l in le_lines[:20])
        if le_lines:
            r.ok(f'{len(le_lines)} BLE device(s) detected')
        else:
            r.info('No BLE devices detected (scan may need discoverable devices nearby)')
    else:
        r.info('hcitool not available (deprecated — use bluetoothctl scan on)')
        r.add_fix(Fix('Install bluetooth tools',
                      ['sudo', 'apt-get', 'install', '-y', 'bluetooth'],
                      needs_root=True, recheck=False))

    r.finalize()
    return r


# ─── Check registry ───────────────────────────────────────────────────────────

CHECKS = [
    {'id': 'system_info',    'name': ' 1. System Info',         'fn': check_system_info,    'slow': False},
    {'id': 'bt_hardware',    'name': ' 2. BT Hardware',         'fn': check_bt_hardware,    'slow': False},
    {'id': 'bt_kernel',      'name': ' 3. Kernel Modules',      'fn': check_bt_kernel,      'slow': False},
    {'id': 'bt_service',     'name': ' 4. Service (bluetoothd)','fn': check_bt_service,     'slow': False},
    {'id': 'bt_interface',   'name': ' 5. HCI Interface',       'fn': check_bt_interface,   'slow': False},
    {'id': 'rfkill',         'name': ' 6. rfkill / Blocked',    'fn': check_rfkill,         'slow': False},
    {'id': 'paired_devices', 'name': ' 7. Paired Devices',      'fn': check_paired_devices, 'slow': False},
    {'id': 'scan',           'name': ' 8. Device Scan  [slow]', 'fn': check_scan,           'slow': True},
    {'id': 'audio_bt',       'name': ' 9. BT Audio',            'fn': check_audio_bt,       'slow': False},
    {'id': 'obex',           'name': '10. OBEX File Transfer',  'fn': check_obex,           'slow': False},
    {'id': 'input_devices',  'name': '11. Input Devices (HID)', 'fn': check_input_devices,  'slow': False},
    {'id': 'power_mgmt',     'name': '12. Power Management',    'fn': check_power_management,'slow': False},
    {'id': 'system_logs',    'name': '13. System Logs',         'fn': check_system_logs,    'slow': False},
    {'id': 'config',         'name': '14. Config & Packages',   'fn': check_config,         'slow': False},
    {'id': 'advanced',       'name': '15. Advanced Tests[slow]','fn': check_advanced,       'slow': True},
]

STATUS_ICON = {
    'notrun':   '○',
    'running':  '↻',
    'ok':       '✓',
    'warning':  '⚠',
    'critical': '✗',
}

STATUS_COLOR = {
    'notrun':   0,
    'running':  3,
    'ok':       2,
    'warning':  3,
    'critical': 4,
}


# ─── TUI ─────────────────────────────────────────────────────────────────────

class BTDiagnosticsTUI:
    LIST_W = 30

    def __init__(self):
        self.checks = [dict(c, result=None) for c in CHECKS]
        self.cursor = 0
        self.output_scroll = 0
        self.focus = 'list'
        self.status_msg = ''
        self.status_is_error = False
        self._list_scroll = 0

    def run(self):
        curses.wrapper(self._main)

    def _main(self, stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)

        while True:
            self._draw(stdscr)
            key = stdscr.getch()

            if key in (ord('q'), ord('Q'), 27):
                break
            elif key == curses.KEY_UP:
                if self.focus == 'list':
                    self.cursor = max(0, self.cursor - 1)
                    self.output_scroll = 0
                else:
                    self.output_scroll = max(0, self.output_scroll - 1)
                self.status_msg = ''
            elif key == curses.KEY_DOWN:
                if self.focus == 'list':
                    self.cursor = min(len(self.checks) - 1, self.cursor + 1)
                    self.output_scroll = 0
                else:
                    self.output_scroll += 1
                self.status_msg = ''
            elif key == curses.KEY_PPAGE:
                h, _ = stdscr.getmaxyx()
                step = h - 4
                if self.focus == 'list':
                    self.cursor = max(0, self.cursor - step)
                else:
                    self.output_scroll = max(0, self.output_scroll - step)
            elif key == curses.KEY_NPAGE:
                h, _ = stdscr.getmaxyx()
                step = h - 4
                if self.focus == 'list':
                    self.cursor = min(len(self.checks) - 1, self.cursor + step)
                else:
                    self.output_scroll += step
            elif key == ord('\t'):
                self.focus = 'output' if self.focus == 'list' else 'list'
            elif key in (ord('r'), ord('R')):
                self._run_check(stdscr, self.cursor)
            elif key in (ord('a'), ord('A')):
                self._run_all(stdscr)
            elif key in (ord('f'), ord('F')):
                self._show_fixes_overlay(stdscr, self.cursor)
            elif key in (ord('s'), ord('S')):
                self._save_report()
            elif key in (ord('?'), ord('h'), ord('H')):
                self._show_help(stdscr)

    # ── Check runners ────────────────────────────────────────────────────────

    def _run_check(self, stdscr, idx):
        check = self.checks[idx]
        check['result'] = 'running'
        self.status_msg = f'Running: {check["name"].strip()} ...'
        self._draw(stdscr)
        stdscr.refresh()
        check['result'] = check['fn']()
        self.output_scroll = 0
        result = check['result']
        fix_hint = f'  (F: {len(result.fixes)} fix(es))' if result.fixes else ''
        self.status_msg = f'Done: {check["name"].strip()} — {result.brief}{fix_hint}'
        self.status_is_error = result.status == 'critical'

    def _run_all(self, stdscr):
        for i in range(len(self.checks)):
            self.cursor = i
            self._run_check(stdscr, i)
            self._draw(stdscr)
            stdscr.refresh()
        self.status_msg = 'All checks complete. Press F to see fixes for highlighted check.'
        self.status_is_error = False

    # ── Fix overlay ──────────────────────────────────────────────────────────

    def _show_fixes_overlay(self, stdscr, check_idx):
        check = self.checks[check_idx]
        result = check['result']

        if not isinstance(result, CheckResult):
            self.status_msg = 'Run this check first (press R).'
            self.status_is_error = False
            return
        if not result.fixes:
            self.status_msg = 'No fixes available for this check.'
            self.status_is_error = False
            return

        fixes = result.fixes
        fix_cursor = 0

        while True:
            h, w = stdscr.getmaxyx()
            self._draw(stdscr)

            max_desc = max(len(f.desc) + (8 if f.needs_root else 0) + 6 for f in fixes)
            max_cmd  = max(len(f.cmd_display()) + 4 for f in fixes)
            box_w = min(max(max_desc, max_cmd, 54), w - 4)
            rows_needed = len(fixes) * 3 + 4
            box_h = min(rows_needed, h - 4)
            box_y = max(0, (h - box_h) // 2)
            box_x = max(0, (w - box_w) // 2)

            title = f' Fixes: {check["name"].strip()} ({len(fixes)}) '
            try:
                stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                stdscr.addstr(box_y, box_x, title[:box_w].ljust(box_w))
                stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass

            row = box_y + 1
            for i, fix in enumerate(fixes):
                if row + 2 >= box_y + box_h - 1:
                    break
                is_sel = (i == fix_cursor)
                root_badge = ' [ROOT]' if fix.needs_root else ''
                arrow = '►' if is_sel else ' '
                desc_line = f' {arrow} {fix.desc}{root_badge}'
                cmd_line  = f'   {fix.cmd_display()}'

                try:
                    if is_sel:
                        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
                        stdscr.addstr(row,     box_x, desc_line[:box_w].ljust(box_w))
                        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
                        stdscr.attron(curses.color_pair(3))
                        stdscr.addstr(row + 1, box_x, cmd_line[:box_w].ljust(box_w))
                        stdscr.attroff(curses.color_pair(3))
                    else:
                        stdscr.addstr(row,     box_x, desc_line[:box_w])
                        stdscr.attron(curses.color_pair(1))
                        stdscr.addstr(row + 1, box_x, cmd_line[:box_w])
                        stdscr.attroff(curses.color_pair(1))
                    stdscr.addstr(row + 2, box_x, ' ' * box_w)
                except curses.error:
                    pass
                row += 3

            footer = ' ↑↓:Select  Enter:Run  Q:Cancel'
            try:
                stdscr.attron(curses.color_pair(5))
                stdscr.addstr(box_y + box_h - 1, box_x, footer[:box_w].ljust(box_w))
                stdscr.attroff(curses.color_pair(5))
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key in (ord('q'), ord('Q'), 27):
                break
            elif key == curses.KEY_UP:
                fix_cursor = max(0, fix_cursor - 1)
            elif key == curses.KEY_DOWN:
                fix_cursor = min(len(fixes) - 1, fix_cursor + 1)
            elif key in (ord('\n'), ord('\r'), curses.KEY_ENTER):
                self._run_fix(stdscr, check_idx, fixes[fix_cursor])
                break

    def _run_fix(self, stdscr, check_idx, fix):
        h, w = stdscr.getmaxyx()
        cmd_str = fix.cmd_display()
        prompt = f' Run: {cmd_str[:w - 20]}? [y/N]: '
        try:
            stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
            stdscr.addstr(h - 1, 0, prompt[:w - 1].ljust(w - 1))
            stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        except curses.error:
            pass
        stdscr.refresh()

        if stdscr.getch() not in (ord('y'), ord('Y')):
            self.status_msg = 'Fix cancelled.'
            self.status_is_error = False
            return

        self.status_msg = f'Applying fix: {fix.desc} ...'
        self._draw(stdscr)
        stdscr.refresh()

        ok, output = fix.run()

        if ok:
            self.status_msg = f'✓ Fix applied: {fix.desc}'
            self.status_is_error = False
        else:
            short_err = output.replace('\n', ' ')[:80]
            self.status_msg = f'✗ Fix failed: {short_err}'
            self.status_is_error = True

        if fix.recheck:
            self._run_check(stdscr, check_idx)

    # ── Save report ──────────────────────────────────────────────────────────

    def _save_report(self):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_dir = Path.home() / '.system_doctor'
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f'bt_report_{ts}.txt'

        lines = [
            '═' * 70,
            f'  BLUETOOTH DIAGNOSTICS REPORT — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}',
            '═' * 70, '',
        ]
        for check in self.checks:
            result = check['result']
            if not isinstance(result, CheckResult):
                continue
            icon = STATUS_ICON.get(result.status, '?')
            lines.append(f'\n{icon} {check["name"].strip()} — {result.brief}')
            lines.append('─' * 60)
            lines.extend(result.lines)
            if result.fixes:
                lines.append('')
                lines.append('  Available fixes:')
                for fix in result.fixes:
                    lines.append(f'    • {fix.desc}')
                    lines.append(f'      {fix.cmd_display()}')

        lines += ['', '═' * 70, '  Issues summary:', '']
        for check in self.checks:
            result = check['result']
            if isinstance(result, CheckResult) and result.issues:
                for sev, msg in result.issues:
                    if sev in ('critical', 'warning'):
                        lines.append(f'  [{sev.upper():8}] {check["name"].strip()}: {msg}')
        lines.append('═' * 70)

        path.write_text('\n'.join(lines))
        self.status_msg = f'Report saved: {path}'
        self.status_is_error = False

    # ── Drawing ──────────────────────────────────────────────────────────────

    def _draw(self, stdscr):
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        list_w = self.LIST_W
        out_x = list_w + 1
        out_w = w - out_x

        title = ' System Doctor — Bluetooth Diagnostics '
        n_ok   = sum(1 for c in self.checks if isinstance(c['result'], CheckResult) and c['result'].status == 'ok')
        n_warn = sum(1 for c in self.checks if isinstance(c['result'], CheckResult) and c['result'].status == 'warning')
        n_crit = sum(1 for c in self.checks if isinstance(c['result'], CheckResult) and c['result'].status == 'critical')
        summary = f' ✓{n_ok} ⚠{n_warn} ✗{n_crit} '
        pad = ' ' * max(0, w - len(title) - len(summary))
        try:
            stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
            stdscr.addstr(0, 0, (title + pad + summary)[:w])
            stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            pass

        for row in range(1, h - 1):
            try:
                stdscr.addch(row, list_w, '│')
            except curses.error:
                pass

        self._draw_list(stdscr, 1, 0, h - 2, list_w)
        self._draw_output(stdscr, 1, out_x, h - 2, out_w)

        cur_result = self.checks[self.cursor]['result']
        fix_count = len(cur_result.fixes) if isinstance(cur_result, CheckResult) else 0
        fix_hint = f'  F:Fix({fix_count})' if fix_count else '  F:Fix'

        if self.status_msg:
            attr = curses.color_pair(4) | curses.A_BOLD if self.status_is_error else curses.color_pair(2) | curses.A_BOLD
            try:
                stdscr.attron(attr)
                stdscr.addstr(h - 1, 0, (' ' + self.status_msg)[:w - 1].ljust(w - 1))
                stdscr.attroff(attr)
            except curses.error:
                pass
        else:
            focus_ind = f'[{"OUT" if self.focus == "output" else "LST"}]'
            footer = f' {focus_ind} R:Run  A:All  S:Save  Tab:Focus  ↑↓:Nav{fix_hint}  Q:Quit  ?:Help'
            try:
                stdscr.attron(curses.color_pair(5))
                stdscr.addstr(h - 1, 0, footer[:w - 1].ljust(w - 1))
                stdscr.attroff(curses.color_pair(5))
            except curses.error:
                pass

        stdscr.refresh()

    def _draw_list(self, stdscr, y0, x0, h, w):
        label = ' Checks' + (' [FOCUS]' if self.focus == 'list' else '')
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(y0, x0, label[:w - 1].ljust(w - 1))
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        visible = h - 1
        if self.cursor < self._list_scroll:
            self._list_scroll = self.cursor
        elif self.cursor >= self._list_scroll + visible:
            self._list_scroll = self.cursor - visible + 1

        for row in range(visible):
            idx = row + self._list_scroll
            if idx >= len(self.checks):
                break
            check = self.checks[idx]
            result = check['result']
            status = 'notrun' if result is None else ('running' if result == 'running' else result.status)
            icon = STATUS_ICON.get(status, '?')
            has_fixes = isinstance(result, CheckResult) and bool(result.fixes)
            fix_dot = '*' if has_fixes else ' '

            line = f'{icon}{fix_dot}{check["name"]}'[:w - 1]
            is_cur = (idx == self.cursor)
            color = STATUS_COLOR.get(status, 0)

            try:
                if is_cur:
                    stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
                    stdscr.addstr(y0 + 1 + row, x0, line.ljust(w - 1))
                    stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
                elif color:
                    stdscr.attron(curses.color_pair(color) | curses.A_BOLD)
                    stdscr.addstr(y0 + 1 + row, x0, line)
                    stdscr.attroff(curses.color_pair(color) | curses.A_BOLD)
                else:
                    stdscr.addstr(y0 + 1 + row, x0, line)
            except curses.error:
                pass

    def _draw_output(self, stdscr, y0, x0, h, w):
        check = self.checks[self.cursor]
        result = check['result']

        fix_count = len(result.fixes) if isinstance(result, CheckResult) else 0
        fix_badge = f'  [{fix_count} fix(es) — press F]' if fix_count else ''
        panel_title = f' {check["name"].strip()}'
        if isinstance(result, CheckResult):
            panel_title += f' — {result.brief}'
        elif result == 'running':
            panel_title += ' — Running...'
        panel_title = (panel_title + fix_badge)[:w - 1]

        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(y0, x0, panel_title.ljust(w - 1))
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        visible = h - 1
        if result is None:
            lines = ['', '  No results yet.', '  Press R to run this check.',
                     '  Press A to run all checks.']
        elif result == 'running':
            lines = ['', '  Running...']
        else:
            lines = result.lines

        max_scroll = max(0, len(lines) - visible)
        self.output_scroll = max(0, min(self.output_scroll, max_scroll))

        for row in range(visible):
            line_idx = row + self.output_scroll
            if line_idx >= len(lines):
                break
            line = lines[line_idx]

            if line.lstrip().startswith('✓'):
                attr = curses.color_pair(2)
            elif line.lstrip().startswith('⚠'):
                attr = curses.color_pair(3)
            elif line.lstrip().startswith('✗'):
                attr = curses.color_pair(4) | curses.A_BOLD
            elif line.lstrip().startswith('──') or line.lstrip().startswith('ℹ'):
                attr = curses.color_pair(1)
            else:
                attr = curses.A_NORMAL

            try:
                stdscr.attron(attr)
                stdscr.addstr(y0 + 1 + row, x0, line[:w - 1])
                stdscr.attroff(attr)
            except curses.error:
                pass

    # ── Help overlay ─────────────────────────────────────────────────────────

    def _show_help(self, stdscr):
        h, w = stdscr.getmaxyx()
        lines = [
            '  BLUETOOTH DIAGNOSTICS — HELP  ',
            '',
            '  Navigation',
            '  ↑ / ↓        Move in focused panel',
            '  PgUp / PgDn  Scroll by page',
            '  Tab          Switch focus: check list ↔ output panel',
            '',
            '  Actions',
            '  R            Run highlighted check',
            '  A            Run ALL checks sequentially',
            '  F            Open fix menu for highlighted check',
            '  S            Save full report to ~/.system_doctor/',
            '  Q / Esc      Quit',
            '  ? / H        Show this help',
            '',
            '  Status icons  (* = fixes available)',
            '  ○  Not run    ↻  Running',
            '  ✓  OK         ⚠  Warning    ✗  Critical',
            '',
            '  Fix menu',
            '  Shows after running a check with issues.',
            '  Each fix shows its command before running.',
            '  [ROOT] = requires sudo.',
            '  After a fix, the check is re-run automatically.',
            '',
            '  Slow checks: 8.Device Scan  15.Advanced Tests',
            '  Root needed: 2.BT Hardware  12.Power Management',
            '',
            '  Press any key to close',
        ]
        box_w = min(max(len(l) for l in lines) + 4, w - 4)
        box_h = min(len(lines) + 2, h - 4)
        box_y = (h - box_h) // 2
        box_x = (w - box_w) // 2

        for i, text in enumerate(lines[:box_h - 2]):
            padded = (' ' + text).ljust(box_w)[:box_w]
            try:
                if i == 0:
                    stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                    stdscr.addstr(box_y + 1 + i, box_x, padded)
                    stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.addstr(box_y + 1 + i, box_x, padded)
            except curses.error:
                pass
        stdscr.refresh()
        stdscr.getch()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Bluetooth diagnostics TUI')
    parser.add_argument('--run-all', action='store_true',
                        help='Run all checks immediately on launch')
    args = parser.parse_args()

    tui = BTDiagnosticsTUI()
    if args.run_all:
        original_main = tui._main

        def patched_main(stdscr):
            curses.curs_set(0)
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)
            tui._run_all(stdscr)
            original_main(stdscr)

        curses.wrapper(patched_main)
    else:
        tui.run()


if __name__ == '__main__':
    main()
