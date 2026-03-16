#!/usr/bin/env python3
"""
Network Diagnostics TUI - part of System Doctor.
19 network checks with two-panel curses interface and contextual fix actions.
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


def _detect_wifi_iface():
    _, lines = _run(['iw', 'dev'])
    for line in lines:
        parts = line.strip().split()
        if parts and parts[0] == 'Interface':
            return parts[1]
    _, lines = _run(['ip', 'link', 'show'])
    for line in lines:
        m = re.search(r'\d+: (wl\S+):', line)
        if m:
            return m.group(1)
    return None


def _detect_gateway():
    _, lines = _run(['ip', 'route', 'show'])
    for line in lines:
        m = re.search(r'default via (\S+)', line)
        if m:
            return m.group(1)
    return None


# ─── Fix ─────────────────────────────────────────────────────────────────────

class Fix:
    """A runnable remediation action tied to a diagnostic result."""

    def __init__(self, desc, cmd, needs_root=False, recheck=True):
        """
        desc      : human-readable description shown in the overlay
        cmd       : list of subprocess args  OR  callable() -> (bool, str)
        needs_root: show [ROOT] badge; we still attempt the run either way
        recheck   : re-run the parent check after applying the fix
        """
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
        self.issues = []  # (severity, message)
        self.fixes = []   # Fix objects

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
    try:
        with open('/etc/os-release') as f:
            r.lines.extend(f'  {l}' for l in f.read().splitlines()[:12])
    except OSError:
        r.lines.append('  /etc/os-release not found')
    r.cmd(['uptime'], 'Uptime')
    r.cmd(['timedatectl', 'status'], 'Date/Time')
    r.finalize()
    return r


def check_network_hw():
    r = CheckResult()
    r.section('PCI Network Adapters')
    _, lines = _run(['lspci'])
    found = [l for l in lines if re.search(r'network|wireless|ethernet|wifi', l, re.I)]
    r.lines.extend(f'  {l}' for l in found) if found else r.lines.append('  (none found)')

    r.section('USB Wireless Adapters')
    _, lines = _run(['lsusb'])
    found = [l for l in lines if re.search(r'wireless|wifi|802\.11|network', l, re.I)]
    r.lines.extend(f'  {l}' for l in found) if found else r.lines.append('  (none found)')

    if _available('lshw'):
        r.cmd(['lshw', '-C', 'network', '-short'], 'lshw Network')
    else:
        r.warning('lshw not installed — detailed hardware info unavailable')
        r.add_fix(Fix('Install lshw',
                      ['sudo', 'apt-get', 'install', '-y', 'lshw'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_drivers():
    r = CheckResult()
    r.section('Loaded Wireless Modules')
    _, lines = _run(['lsmod'])
    mods = [l for l in lines if re.search(r'wifi|wireless|80211|iwl|ath|rtw|rt2|mt7|brcm', l, re.I)]
    if mods:
        r.lines.extend(f'  {l}' for l in mods)
        mod_names = [l.split()[0] for l in mods]
        r.ok(f'Wireless modules loaded: {", ".join(mod_names[:5])}')

        for mod in mod_names[:3]:
            r.add_fix(Fix(f'Reload module {mod}',
                          lambda m=mod: (
                              _run(['sudo', 'modprobe', '-r', m])[0] == 0 and
                              _run(['sudo', 'modprobe', m])[0] == 0,
                              f'modprobe -r {m} && modprobe {m}'
                          ),
                          needs_root=True, recheck=True))
    else:
        r.critical('No wireless kernel modules detected')
        r.add_fix(Fix('Try loading iwlwifi (Intel)',
                      ['sudo', 'modprobe', 'iwlwifi'],
                      needs_root=True, recheck=True))
        r.add_fix(Fix('Try loading ath9k (Atheros)',
                      ['sudo', 'modprobe', 'ath9k'],
                      needs_root=True, recheck=True))

    r.section('dmesg wireless warnings/errors')
    _, lines = _run(['dmesg', '--level=err,warn'], timeout=5)
    wl = [l for l in lines if re.search(r'wifi|wlan|wireless|firmware|80211', l, re.I)]
    if wl:
        r.lines.extend(f'  {l}' for l in wl[-20:])
        r.warning(f'{len(wl)} wireless-related kernel warnings/errors in dmesg')
        r.add_fix(Fix('Reinstall Linux firmware packages',
                      ['sudo', 'apt-get', 'install', '--reinstall', '-y', 'linux-firmware'],
                      needs_root=True, recheck=True))
    else:
        r.ok('No wireless errors in dmesg')

    r.finalize()
    return r


def check_interfaces():
    r = CheckResult()
    r.cmd(['ip', 'link', 'show'], 'Interfaces (ip link)')
    r.cmd(['ip', 'addr', 'show'], 'IP Addresses (ip addr)')
    if _available('iwconfig'):
        r.cmd(['iwconfig'], 'Wireless (iwconfig)')
    else:
        r.info('iwconfig not available (apt install wireless-tools)')

    # Check for WiFi interface that is DOWN
    iface = _detect_wifi_iface()
    if iface:
        _, link_lines = _run(['ip', 'link', 'show', iface])
        for line in link_lines:
            if 'DOWN' in line and 'UP' not in line:
                r.warning(f'Interface {iface} is DOWN')
                r.add_fix(Fix(f'Bring up {iface}',
                              ['sudo', 'ip', 'link', 'set', iface, 'up'],
                              needs_root=True, recheck=True))
                break

    r.finalize()
    return r


def check_wifi():
    r = CheckResult()
    iface = _detect_wifi_iface()
    if not iface:
        r.critical('No WiFi interface detected')
        r.info('Check: ip link | grep wl')
        r.add_fix(Fix('Restart NetworkManager (may expose interface)',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))
        r.finalize()
        return r
    r.ok(f'WiFi interface: {iface}')

    if _available('iw'):
        r.section(f'Interface info ({iface})')
        r.cmd(['iw', 'dev', iface, 'info'])

        r.section('Link status')
        _, link_lines = _run(['iw', 'dev', iface, 'link'])
        r.lines.extend(f'  {l}' for l in link_lines)
        if any('Connected' in l for l in link_lines):
            r.ok('WiFi connected')
        else:
            r.warning('WiFi not connected to any network')
            r.add_fix(Fix(f'Connect {iface} via NetworkManager',
                          ['nmcli', 'device', 'connect', iface],
                          needs_root=False, recheck=True))
            r.add_fix(Fix('Restart NetworkManager',
                          ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                          needs_root=True, recheck=True))
    else:
        r.warning('iw not available (apt install iw)')
        r.add_fix(Fix('Install iw',
                      ['sudo', 'apt-get', 'install', '-y', 'iw'],
                      needs_root=True, recheck=True))

    if _available('iwconfig'):
        r.section('Signal & power management')
        _, iw_lines = _run(['iwconfig', iface])
        r.lines.extend(f'  {l}' for l in iw_lines)

        for line in iw_lines:
            m = re.search(r'Signal level=(-\d+)', line)
            if m:
                sig = int(m.group(1))
                if sig < -80:
                    r.critical(f'Signal very weak: {sig} dBm (threshold: -80)')
                elif sig < -70:
                    r.warning(f'Signal weak: {sig} dBm (threshold: -70)')
                else:
                    r.ok(f'Signal OK: {sig} dBm')

            if 'Power Management:on' in line:
                r.warning('WiFi Power Management ON — may cause random disconnects')
                r.add_fix(Fix(f'Disable Power Management on {iface}',
                              ['sudo', 'iwconfig', iface, 'power', 'off'],
                              needs_root=True, recheck=True))

    r.section('Available networks (nmcli)')
    if _available('nmcli'):
        r.cmd(['nmcli', 'dev', 'wifi', 'list'], timeout=10)
    else:
        r.info('nmcli not available')

    r.finalize()
    return r


def check_network_manager():
    r = CheckResult()
    if not _available('nmcli'):
        r.info('NetworkManager (nmcli) not available')
        r.finalize()
        return r

    r.cmd(['nmcli', 'general', 'status'], 'NM General Status')
    r.cmd(['nmcli', 'device', 'status'], 'Device Status')
    r.cmd(['nmcli', 'connection', 'show', '--active'], 'Active Connections')

    # Always offer restart as a useful fix
    r.add_fix(Fix('Restart NetworkManager',
                  ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                  needs_root=True, recheck=True))

    r.section('wpa_supplicant')
    _, lines = _run(['pgrep', '-x', 'wpa_supplicant'])
    if lines:
        r.ok('wpa_supplicant is running')
        r.add_fix(Fix('Restart wpa_supplicant',
                      ['sudo', 'systemctl', 'restart', 'wpa_supplicant'],
                      needs_root=True, recheck=True))
    else:
        r.warning('wpa_supplicant NOT running')
        r.add_fix(Fix('Start wpa_supplicant',
                      ['sudo', 'systemctl', 'start', 'wpa_supplicant'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_routing():
    r = CheckResult()
    r.cmd(['ip', 'route', 'show'], 'Routing Table')

    gw = _detect_gateway()
    r.section('Default Gateway')
    if gw:
        r.ok(f'Default gateway: {gw}')
        _, arp = _run(['ip', 'neigh', 'show'])
        if any(gw in l for l in arp):
            r.ok(f'Gateway {gw} is in ARP table')
        else:
            r.warning(f'Gateway {gw} not in ARP table — may be unreachable')
            r.add_fix(Fix('Flush neighbour/ARP cache',
                          ['sudo', 'ip', 'neigh', 'flush', 'all'],
                          needs_root=True, recheck=True))
    else:
        r.critical('No default gateway — internet impossible')
        iface = _detect_wifi_iface()
        if iface:
            r.add_fix(Fix(f'Renew DHCP on {iface}',
                          ['sudo', 'dhclient', iface],
                          needs_root=True, recheck=True))
        r.add_fix(Fix('Restart NetworkManager',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))

    r.cmd(['ip', 'neigh', 'show'], 'ARP Table')
    r.finalize()
    return r


def check_dns():
    r = CheckResult()
    dns_broken = False

    r.section('/etc/resolv.conf')
    try:
        with open('/etc/resolv.conf') as f:
            content = f.read()
        r.lines.extend(f'  {l}' for l in content.splitlines())
        if 'nameserver' not in content:
            r.critical('No nameserver in /etc/resolv.conf')
            dns_broken = True
        else:
            servers = [l.split()[1] for l in content.splitlines()
                       if l.startswith('nameserver')]
            r.ok(f'DNS servers: {", ".join(servers)}')
    except OSError:
        r.critical('/etc/resolv.conf not readable')
        dns_broken = True

    if _available('resolvectl'):
        r.cmd(['resolvectl', 'status'], 'resolvectl status')

    r.section('DNS resolution tests')
    failed = []
    for domain in ['google.com', 'cloudflare.com']:
        rc, lines = _run(['nslookup', domain], timeout=8)
        if rc == 0:
            r.ok(f'Resolved {domain}')
        else:
            r.critical(f'DNS resolution FAILED for {domain}')
            r.lines.extend(f'  {l}' for l in lines[:5])
            failed.append(domain)

    if failed:
        dns_broken = True

    r.section('Alternative DNS servers')
    for dns_ip, name in [('8.8.8.8', 'Google'), ('1.1.1.1', 'Cloudflare')]:
        rc, _ = _run(['nslookup', 'google.com', dns_ip], timeout=8)
        if rc == 0:
            r.ok(f'{name} DNS ({dns_ip}) works')
        else:
            r.warning(f'{name} DNS ({dns_ip}) unreachable')

    if dns_broken:
        r.add_fix(Fix(
            'Set Google + Cloudflare DNS in /etc/resolv.conf',
            lambda: (
                lambda rc, _: rc == 0
            )(_run(['sudo', 'bash', '-c',
                    'echo "nameserver 8.8.8.8\nnameserver 1.1.1.1" > /etc/resolv.conf'])[0], None),
            needs_root=True, recheck=True))
        if _available('resolvectl'):
            r.add_fix(Fix('Restart systemd-resolved',
                          ['sudo', 'systemctl', 'restart', 'systemd-resolved'],
                          needs_root=True, recheck=True))
        r.add_fix(Fix('Restart NetworkManager (may restore DNS)',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_connectivity():
    r = CheckResult()
    gw = _detect_gateway()
    any_failure = False

    if gw:
        r.section(f'Ping gateway ({gw})')
        rc, lines = _run(['ping', '-c', '4', '-W', '2', gw], timeout=15)
        r.lines.extend(f'  {l}' for l in lines)
        if rc == 0:
            loss = next((re.search(r'(\d+)% packet loss', l) for l in lines if 'packet loss' in l), None)
            loss_pct = int(loss.group(1)) if loss else 0
            if loss_pct > 0:
                r.warning(f'Packet loss to gateway: {loss_pct}%')
                any_failure = True
            else:
                r.ok('Gateway reachable, 0% loss')
        else:
            r.critical('Gateway NOT reachable — local network issue')
            any_failure = True
    else:
        r.warning('Skipping gateway ping (no default gateway)')
        any_failure = True

    r.section('Ping 8.8.8.8 (internet via IP)')
    rc, lines = _run(['ping', '-c', '4', '-W', '3', '8.8.8.8'], timeout=20)
    r.lines.extend(f'  {l}' for l in lines)
    if rc == 0:
        loss = next((re.search(r'(\d+)% packet loss', l) for l in lines if 'packet loss' in l), None)
        loss_pct = int(loss.group(1)) if loss else 0
        if loss_pct > 5:
            r.warning(f'Packet loss to 8.8.8.8: {loss_pct}%')
            any_failure = True
        else:
            r.ok('Internet reachable via IP')
    else:
        r.critical('Internet NOT reachable via IP')
        any_failure = True

    r.section('Ping google.com (DNS + internet)')
    rc, lines = _run(['ping', '-c', '4', '-W', '3', 'google.com'], timeout=20)
    r.lines.extend(f'  {l}' for l in lines)
    if rc == 0:
        r.ok('google.com reachable')
    else:
        r.critical('google.com NOT reachable')
        r.info('If 8.8.8.8 works but google.com fails → DNS problem')
        any_failure = True

    if any_failure:
        r.add_fix(Fix('Restart NetworkManager',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))
        r.add_fix(Fix('Flush routing cache',
                      ['sudo', 'ip', 'route', 'flush', 'cache'],
                      needs_root=True, recheck=True))
        iface = _detect_wifi_iface()
        if iface:
            r.add_fix(Fix(f'Renew DHCP on {iface}',
                          ['sudo', 'dhclient', '-r', iface],
                          needs_root=True, recheck=True))

    r.finalize()
    return r


def check_latency():
    r = CheckResult()
    gw = _detect_gateway()
    high_latency = False

    r.section('Extended ping to 8.8.8.8 (20 packets)')
    rc, lines = _run(['ping', '-c', '20', '-i', '0.3', '8.8.8.8'], timeout=30)
    r.lines.extend(f'  {l}' for l in lines)
    for line in lines:
        m = re.search(r'(\d+)% packet loss', line)
        if m and int(m.group(1)) > 5:
            r.warning(f'Packet loss: {m.group(1)}%')
            high_latency = True
        m2 = re.search(r'rtt.+= [\d.]+/([\d.]+)/', line)
        if m2:
            avg = float(m2.group(1))
            if avg > 150:
                r.warning(f'High average latency: {avg:.1f}ms')
                high_latency = True
            else:
                r.ok(f'Average latency: {avg:.1f}ms')

    if gw:
        r.section(f'Extended ping to gateway {gw} (20 packets)')
        r.cmd(['ping', '-c', '20', '-i', '0.3', gw], timeout=30)

    r.section('MTU discovery')
    for size in [1472, 1400, 1300]:
        rc, _ = _run(['ping', '-c', '2', '-M', 'do', '-s', str(size), '8.8.8.8'], timeout=10)
        if rc == 0:
            r.ok(f'MTU {size} bytes: OK')
        else:
            r.warning(f'MTU {size} bytes: failed (possible fragmentation)')

    if high_latency:
        iface = _detect_wifi_iface()
        if iface and _available('iwconfig'):
            r.add_fix(Fix(f'Disable WiFi Power Management on {iface}',
                          ['sudo', 'iwconfig', iface, 'power', 'off'],
                          needs_root=True, recheck=True))
        r.add_fix(Fix('Restart NetworkManager',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_bandwidth():
    r = CheckResult()

    if _available('curl'):
        r.section('Download test (Cloudflare, 10 MB)')
        rc, lines = _run([
            'curl', '-o', '/dev/null', '-s', '-w',
            'Speed: %{speed_download} B/s\nTime: %{time_total}s\nSize: %{size_download} bytes\n',
            'http://speed.cloudflare.com/__down?bytes=10000000'
        ], timeout=60)
        r.lines.extend(f'  {l}' for l in lines)
        for line in lines:
            m = re.search(r'Speed: ([\d.]+)', line)
            if m:
                bps = float(m.group(1))
                mbps = bps * 8 / 1_000_000
                if mbps < 1:
                    r.warning(f'Download speed very slow: {mbps:.2f} Mbps')
                else:
                    r.ok(f'Download speed: {mbps:.1f} Mbps')
    else:
        r.warning('curl not available')
        r.add_fix(Fix('Install curl',
                      ['sudo', 'apt-get', 'install', '-y', 'curl'],
                      needs_root=True, recheck=True))

    if _available('speedtest-cli'):
        r.cmd(['speedtest-cli', '--simple'], 'speedtest-cli', timeout=120)
    else:
        r.info('speedtest-cli not installed')
        r.add_fix(Fix('Install speedtest-cli (pip)',
                      ['pip', 'install', '--user', 'speedtest-cli'],
                      needs_root=False, recheck=True))
        r.add_fix(Fix('Install speedtest-cli (apt)',
                      ['sudo', 'apt-get', 'install', '-y', 'speedtest-cli'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


def check_firewall():
    r = CheckResult()
    is_root = os.geteuid() == 0

    if _available('ufw'):
        r.cmd(['ufw', 'status', 'verbose'], 'UFW Status')
        _, lines = _run(['ufw', 'status'])
        if any('active' in l.lower() for l in lines):
            r.info('UFW is active — if blocking connections, check rules below')
            r.add_fix(Fix('Temporarily disable UFW (testing only)',
                          ['sudo', 'ufw', 'disable'],
                          needs_root=True, recheck=True))
            r.add_fix(Fix('Allow all outgoing traffic in UFW',
                          ['sudo', 'ufw', 'default', 'allow', 'outgoing'],
                          needs_root=True, recheck=True))
    else:
        r.info('ufw not installed')

    r.section('iptables')
    if is_root:
        r.cmd(['iptables', '-L', '-n', '--line-numbers'], timeout=10)
        r.add_fix(Fix('Flush all iptables rules (caution: removes all rules)',
                      ['sudo', 'iptables', '-F'],
                      needs_root=True, recheck=True))
    else:
        r.info('iptables requires root (run with sudo for full details)')

    if _available('firewall-cmd'):
        rc, lines = _run(['systemctl', 'is-active', 'firewalld'])
        if rc == 0:
            r.cmd(['firewall-cmd', '--list-all'], 'firewalld')
            r.add_fix(Fix('Stop firewalld (temporary)',
                          ['sudo', 'systemctl', 'stop', 'firewalld'],
                          needs_root=True, recheck=True))
        else:
            r.info('firewalld not active')

    r.finalize()
    return r


def check_ports():
    r = CheckResult()
    r.cmd(['ss', '-tuln'], 'Listening Ports (ss)')
    r.section('Established connections (ss, first 30)')
    rc, lines = _run(['ss', '-tun', 'state', 'established'])
    r.lines.extend(f'  {l}' for l in lines[:30])
    if len(lines) > 30:
        r.info(f'({len(lines) - 30} more lines truncated)')
    r.finalize()
    return r


def check_dhcp():
    r = CheckResult()
    r.section('DHCP leases (/var/lib/dhcp)')
    dhcp_path = Path('/var/lib/dhcp')
    if dhcp_path.exists():
        files = list(dhcp_path.iterdir())
        r.info(f'Files: {[f.name for f in files]}')
        for f in files:
            if f.suffix in ('.lease', '.leases'):
                try:
                    r.lines.extend(f'  {l}' for l in f.read_text().splitlines()[:30])
                except OSError:
                    r.info(f'{f.name}: permission denied')
    else:
        r.info('/var/lib/dhcp not found')

    r.section('NetworkManager leases')
    nm_path = Path('/var/lib/NetworkManager')
    if nm_path.exists():
        for f in list(nm_path.glob('*.lease'))[:2]:
            try:
                r.lines.extend(f'  {l}' for l in f.read_text().splitlines()[:20])
            except OSError:
                r.info(f'{f.name}: permission denied')
    else:
        r.info('/var/lib/NetworkManager not found')

    iface = _detect_wifi_iface()
    if iface:
        r.add_fix(Fix(f'Renew DHCP lease on {iface} (dhclient)',
                      ['sudo', 'dhclient', '-r', iface],
                      needs_root=True, recheck=True))
        r.add_fix(Fix(f'Reconnect {iface} via nmcli (renews DHCP)',
                      ['nmcli', 'device', 'reapply', iface],
                      needs_root=False, recheck=True))

    r.finalize()
    return r


def check_proxy_vpn():
    r = CheckResult()
    r.section('Proxy environment variables')
    proxy_vars = {k: v for k, v in os.environ.items() if 'proxy' in k.lower()}
    if proxy_vars:
        r.warning(f'Proxy env vars set: {list(proxy_vars.keys())}')
        for k, v in proxy_vars.items():
            r.lines.append(f'  {k}={v}')
        r.info('To unset: run the commands below in your shell, then restart the app')
        for k in proxy_vars:
            r.lines.append(f'  unset {k}')

        def _unset_proxy():
            for k in list(proxy_vars.keys()):
                os.environ.pop(k, None)
            return True, f'Unset {len(proxy_vars)} proxy variable(s) in this process'

        r.add_fix(Fix('Unset proxy variables (this process only)',
                      _unset_proxy, needs_root=False, recheck=True))
    else:
        r.ok('No proxy environment variables set')

    r.section('VPN / TUN interfaces')
    _, lines = _run(['ip', 'link', 'show'])
    tun_lines = [l for l in lines if re.search(r'\btun\b|\btap\b|\bwg\b|vpn', l, re.I)]
    if tun_lines:
        r.warning('TUN/TAP/VPN interface detected')
        r.lines.extend(f'  {l}' for l in tun_lines)
        if _available('nmcli'):
            _, vpn = _run(['nmcli', 'connection', 'show', '--active'])
            vpn_conns = [l for l in vpn if 'vpn' in l.lower()]
            for conn in vpn_conns:
                name = conn.split()[0]
                r.add_fix(Fix(f'Disconnect VPN: {name}',
                              ['nmcli', 'connection', 'down', name],
                              needs_root=False, recheck=True))
    else:
        r.ok('No VPN interfaces detected')

    r.finalize()
    return r


def check_interface_stats():
    r = CheckResult()
    r.section('/proc/net/dev')
    try:
        with open('/proc/net/dev') as f:
            r.lines.extend(f'  {l}' for l in f.read().splitlines())
    except OSError:
        r.warning('Cannot read /proc/net/dev')

    iface = _detect_wifi_iface()
    if iface:
        r.cmd(['ip', '-s', 'link', 'show', iface], f'Statistics for {iface}')
        _, stat_lines = _run(['ip', '-s', 'link', 'show', iface])
        for line in stat_lines:
            m = re.search(r'errors (\d+)', line)
            if m and int(m.group(1)) > 0:
                r.warning(f'Interface errors detected: {line.strip()}')
                r.add_fix(Fix(f'Reset/restart interface {iface}',
                              ['sudo', 'ip', 'link', 'set', iface, 'down'],
                              needs_root=True, recheck=False))

    r.finalize()
    return r


def check_system_logs():
    r = CheckResult()
    r.section('NetworkManager journal (last 30 min)')
    rc, lines = _run(
        ['journalctl', '-u', 'NetworkManager', '--since', '30 minutes ago',
         '--no-pager', '-n', '50'], timeout=10)
    if rc == 0:
        r.lines.extend(f'  {l}' for l in lines)
        errors = [l for l in lines if re.search(r'error|fail|disconnect', l, re.I)]
        if errors:
            r.warning(f'{len(errors)} NM error/disconnect events in last 30min')
            r.add_fix(Fix('Restart NetworkManager',
                          ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                          needs_root=True, recheck=True))
    else:
        r.info('journalctl not accessible (try sudo)')

    r.section('wpa_supplicant journal (last 30 min)')
    rc, lines = _run(
        ['journalctl', '-u', 'wpa_supplicant', '--since', '30 minutes ago',
         '--no-pager', '-n', '30'], timeout=10)
    if rc == 0:
        r.lines.extend(f'  {l}' for l in lines)
        wpa_errors = [l for l in lines if re.search(r'error|fail|deauth', l, re.I)]
        if wpa_errors:
            r.warning(f'{len(wpa_errors)} wpa_supplicant errors in last 30min')
            r.add_fix(Fix('Restart wpa_supplicant',
                          ['sudo', 'systemctl', 'restart', 'wpa_supplicant'],
                          needs_root=True, recheck=True))
    else:
        r.info('wpa_supplicant logs not accessible')

    r.section('dmesg wireless (recent)')
    _, lines = _run(['dmesg', '--time-format', 'reltime'], timeout=5)
    wl = [l for l in lines if re.search(r'wlan|wifi|wireless|80211', l, re.I)]
    r.lines.extend(f'  {l}' for l in wl[-30:])
    if not wl:
        r.info('No wireless messages in dmesg')

    r.finalize()
    return r


def check_auth():
    r = CheckResult()
    r.section('wpa_supplicant process')
    rc, lines = _run(['pgrep', '-a', 'wpa_supplicant'])
    if rc == 0:
        r.ok('wpa_supplicant is running')
        r.lines.extend(f'  {l}' for l in lines)
    else:
        r.warning('wpa_supplicant NOT running')
        r.add_fix(Fix('Start wpa_supplicant',
                      ['sudo', 'systemctl', 'start', 'wpa_supplicant'],
                      needs_root=True, recheck=True))
        r.add_fix(Fix('Enable + start wpa_supplicant',
                      ['sudo', 'systemctl', 'enable', '--now', 'wpa_supplicant'],
                      needs_root=True, recheck=True))

    r.section('SSL certificates')
    cert_dir = Path('/etc/ssl/certs')
    if cert_dir.exists():
        count = len(list(cert_dir.iterdir()))
        if count < 10:
            r.warning(f'Very few SSL certificates: {count} (expected 100+)')
            r.add_fix(Fix('Update CA certificates',
                          ['sudo', 'update-ca-certificates', '--fresh'],
                          needs_root=True, recheck=True))
        else:
            r.ok(f'SSL certificates: {count} in /etc/ssl/certs')
    else:
        r.warning('/etc/ssl/certs not found')
        r.add_fix(Fix('Install ca-certificates',
                      ['sudo', 'apt-get', 'install', '-y', 'ca-certificates'],
                      needs_root=True, recheck=True))

    r.section('wpa_supplicant.conf')
    conf = Path('/etc/wpa_supplicant/wpa_supplicant.conf')
    if conf.exists():
        r.ok('wpa_supplicant.conf found')
        try:
            lines = conf.read_text().splitlines()
            safe = [l for l in lines if not re.search(r'psk=|password=|passwd=', l, re.I)]
            r.lines.extend(f'  {l}' for l in safe[:30])
        except PermissionError:
            r.info('File requires root to read')
    else:
        r.info('No wpa_supplicant.conf (typically managed by NetworkManager)')

    r.finalize()
    return r


def check_advanced():
    r = CheckResult()
    net_broken = False

    if _available('curl'):
        r.section('HTTPS test (google.com)')
        rc, lines = _run(['curl', '-I', '-s', '-m', '10', 'https://google.com'], timeout=15)
        r.lines.extend(f'  {l}' for l in lines[:10])
        if rc == 0 and any('HTTP' in l for l in lines):
            r.ok('HTTPS working')
        else:
            r.critical('HTTPS NOT working')
            net_broken = True

        r.section('HTTP test (neverssl.com)')
        rc, lines = _run(['curl', '-I', '-s', '-m', '10', 'http://neverssl.com'], timeout=15)
        r.lines.extend(f'  {l}' for l in lines[:10])
        if rc == 0 and any('HTTP' in l for l in lines):
            r.ok('HTTP working')
        else:
            r.critical('HTTP NOT working')
            net_broken = True
    else:
        r.warning('curl not available — skipping HTTP/HTTPS tests')
        r.add_fix(Fix('Install curl',
                      ['sudo', 'apt-get', 'install', '-y', 'curl'],
                      needs_root=True, recheck=True))

    if _available('dig'):
        r.cmd(['dig', '@8.8.8.8', 'google.com', '+short'], 'dig DNS test')
    else:
        r.info('dig not installed')
        r.add_fix(Fix('Install dig (dnsutils)',
                      ['sudo', 'apt-get', 'install', '-y', 'dnsutils'],
                      needs_root=True, recheck=True))

    r.section('NTP / Time sync')
    rc, lines = _run(['timedatectl', 'status'])
    r.lines.extend(f'  {l}' for l in lines)
    synced = any('synchronized: yes' in l.lower() for l in lines)
    if synced:
        r.ok('NTP synchronized')
    else:
        r.warning('NTP not synchronized')
        r.add_fix(Fix('Enable NTP synchronization',
                      ['sudo', 'timedatectl', 'set-ntp', 'true'],
                      needs_root=True, recheck=True))

    if _available('traceroute'):
        r.section('Traceroute to 8.8.8.8 (max 15 hops)')
        rc, lines = _run(['traceroute', '-m', '15', '-w', '2', '8.8.8.8'], timeout=60)
        r.lines.extend(f'  {l}' for l in lines)
        if sum(1 for l in lines if '* * *' in l) > 5:
            r.warning('Many timeout hops in traceroute')
    elif _available('tracepath'):
        r.cmd(['tracepath', '-m', '15', '8.8.8.8'], 'tracepath', timeout=60)
    else:
        r.info('traceroute/tracepath not installed')
        r.add_fix(Fix('Install traceroute',
                      ['sudo', 'apt-get', 'install', '-y', 'traceroute'],
                      needs_root=True, recheck=True))

    if net_broken:
        r.add_fix(Fix('Restart NetworkManager',
                      ['sudo', 'systemctl', 'restart', 'NetworkManager'],
                      needs_root=True, recheck=True))

    r.finalize()
    return r


# ─── Check registry ───────────────────────────────────────────────────────────

CHECKS = [
    {'id': 'system_info',  'name': ' 1. System Info',         'fn': check_system_info,     'slow': False},
    {'id': 'network_hw',   'name': ' 2. Network Hardware',    'fn': check_network_hw,      'slow': False},
    {'id': 'drivers',      'name': ' 3. Drivers & Modules',   'fn': check_drivers,         'slow': False},
    {'id': 'interfaces',   'name': ' 4. Interfaces',          'fn': check_interfaces,      'slow': False},
    {'id': 'wifi',         'name': ' 5. WiFi Connection',     'fn': check_wifi,            'slow': False},
    {'id': 'net_manager',  'name': ' 6. NetworkManager',      'fn': check_network_manager, 'slow': False},
    {'id': 'routing',      'name': ' 7. Routing & Gateway',   'fn': check_routing,         'slow': False},
    {'id': 'dns',          'name': ' 8. DNS',                 'fn': check_dns,             'slow': False},
    {'id': 'connectivity', 'name': ' 9. Connectivity',        'fn': check_connectivity,    'slow': False},
    {'id': 'latency',      'name': '10. Latency & MTU',       'fn': check_latency,         'slow': True},
    {'id': 'bandwidth',    'name': '11. Bandwidth  [slow]',   'fn': check_bandwidth,       'slow': True},
    {'id': 'firewall',     'name': '12. Firewall',            'fn': check_firewall,        'slow': False},
    {'id': 'ports',        'name': '13. Ports & Connections', 'fn': check_ports,           'slow': False},
    {'id': 'dhcp',         'name': '14. DHCP',                'fn': check_dhcp,            'slow': False},
    {'id': 'proxy_vpn',    'name': '15. Proxy & VPN',         'fn': check_proxy_vpn,       'slow': False},
    {'id': 'iface_stats',  'name': '16. Interface Stats',     'fn': check_interface_stats, 'slow': False},
    {'id': 'system_logs',  'name': '17. System Logs',         'fn': check_system_logs,     'slow': False},
    {'id': 'auth',         'name': '18. Auth & Certs',        'fn': check_auth,            'slow': False},
    {'id': 'advanced',     'name': '19. Advanced Tests',      'fn': check_advanced,        'slow': True},
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

class NetDiagnosticsTUI:
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
            self._draw(stdscr)   # keep background fresh

            # Overlay dimensions
            max_desc = max(len(f.desc) + (8 if f.needs_root else 0) + 6 for f in fixes)
            max_cmd  = max(len(f.cmd_display()) + 4 for f in fixes)
            box_w = min(max(max_desc, max_cmd, 54), w - 4)
            rows_needed = len(fixes) * 3 + 4
            box_h = min(rows_needed, h - 4)
            box_y = max(0, (h - box_h) // 2)
            box_x = max(0, (w - box_w) // 2)

            # Title bar
            title = f' Fixes: {check["name"].strip()} ({len(fixes)}) '
            try:
                stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                stdscr.addstr(box_y, box_x, title[:box_w].ljust(box_w))
                stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass

            # Fix items
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
                    # blank separator
                    stdscr.addstr(row + 2, box_x, ' ' * box_w)
                except curses.error:
                    pass
                row += 3

            # Footer
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
                break   # close overlay after running (check re-ran, fixes may have changed)

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
        path = out_dir / f'net_report_{ts}.txt'

        lines = [
            '═' * 70,
            f'  NETWORK DIAGNOSTICS REPORT — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}',
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

        # Title bar
        title = ' System Doctor — Network Diagnostics '
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

        # Divider
        for row in range(1, h - 1):
            try:
                stdscr.addch(row, list_w, '│')
            except curses.error:
                pass

        self._draw_list(stdscr, 1, 0, h - 2, list_w)
        self._draw_output(stdscr, 1, out_x, h - 2, out_w)

        # Footer
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

            # Fix indicator
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
            '  NETWORK DIAGNOSTICS — HELP  ',
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
            '  Slow checks: 10.Latency  11.Bandwidth  19.Advanced',
            '  Root needed: 12.Firewall  17.System Logs',
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
    parser = argparse.ArgumentParser(description='Network diagnostics TUI')
    parser.add_argument('--run-all', action='store_true',
                        help='Run all checks immediately on launch')
    args = parser.parse_args()

    tui = NetDiagnosticsTUI()
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
