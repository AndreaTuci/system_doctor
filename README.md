# System Doctor

Linux system toolkit with interactive TUI. Two tools, one launcher.

## Launch

```bash
python3 system_doctor.py          # Main menu
```

Or run tools directly:

```bash
python3 disk_cleaner.py           # Disk cleaner (same as find_big_files.py)
python3 net_diagnostics.py        # Network diagnostics
python3 net_diagnostics.py --run-all   # Auto-run all checks on launch
```

Use `sudo` for full access:

```bash
sudo python3 system_doctor.py
sudo python3 net_diagnostics.py
```

## Tools

### 1. Disk Cleaner

Find large files, directories, Docker images and containers. Select and delete interactively.
Every row carries a risk badge saying what deleting it costs you.

| Key | Action |
|-----|--------|
| ↑↓ | Navigate |
| Space | Toggle selection |
| A | Select / deselect all |
| 1 2 3 4 | Toggle every row at one risk level (CRITICAL / CAUTION / UNKNOWN / SAFE) |
| U | Auto-select UNUSED images + STOPPED containers |
| D | Delete selected (asks confirmation) |
| Q | Quit |
| ? | Help overlay |

**Risk badges**

| Badge | Level | Means | Categories |
|-------|-------|-------|------------|
| `███` | CRITICAL | unrecoverable | `git-history`, `db-live` |
| `██░` | CAUTION | probably no second copy | `media`, `dump`, running containers |
| `█░░` | UNKNOWN | not identified — the cleaner cannot vouch for it | `other` |
| `░░░` | SAFE | regenerates | `venv`, `node_mod`, `cache`, unreferenced images |

Classification is by path segment, never by age: **an untouched file is not a safe file.** A git
pack from an old clone reads as cold precisely because it is history — that is what it is for.
The cleaner never refuses a deletion, but when the selection contains a CRITICAL item the
confirmation asks you to type `CRITICAL` rather than a single keypress.

Untagged Docker images (`<none>:<none>`) are resolved to their Compose origin via image labels,
so a row reads `legacoop/astro (dangling)` instead of nothing. Sizes come from
`docker system df -v`: the column shows what deleting that image actually reclaims, with
`+N MB shared` naming the layers you only get back by deleting every image that uses them
(`docker images` alone double-counts those, over-reporting a group of four by ~4×).

Images are listed **stalest first**, so the ones you are least likely to still need float to the
top. Read the age label carefully, because Docker stores no last-use timestamp on an image:

| Label | Meaning |
|-------|---------|
| `run 4mo` | real signal — a container built on this image last started then |
| `built 3y` | no container ever ran; this is only the build/pull date |

On a typical machine most rows are `built` (here: 138 of 166). The ordering changes what you see
first, never what is safe — that is the badge's job.

### 2. Network Diagnostics

19 checks covering the full network stack. Two-panel TUI: check list on the left, scrollable output on the right.

| Key | Action |
|-----|--------|
| ↑↓ | Navigate check list (or scroll output) |
| Tab | Switch focus between list and output panel |
| R | Run highlighted check |
| A | Run all checks sequentially |
| S | Save full report to `~/.system_doctor/` |
| F | Open fix menu for highlighted check (`*` in list = fixes available) |
| Q | Quit |
| ? | Help overlay |

**Checks:**

| # | Check | Notes |
|---|-------|-------|
| 1 | System Info | OS, kernel, uptime, timezone |
| 2 | Network Hardware | PCI/USB adapters, lshw |
| 3 | Drivers & Modules | Kernel modules, dmesg errors |
| 4 | Interfaces | ip link, ip addr, iwconfig |
| 5 | WiFi Connection | Signal strength, SSID, power management |
| 6 | NetworkManager | nmcli status, active connections |
| 7 | Routing & Gateway | Default GW, ARP table |
| 8 | DNS | resolv.conf, nslookup tests (Google + Cloudflare) |
| 9 | Connectivity | Ping gateway, 8.8.8.8, google.com |
| 10 | Latency & MTU | 20-packet ping, MTU discovery — **slow** |
| 11 | Bandwidth | curl download test, speedtest-cli — **slow** |
| 12 | Firewall | ufw, iptables (root), firewalld |
| 13 | Ports & Connections | ss -tuln, established connections |
| 14 | DHCP | Lease files |
| 15 | Proxy & VPN | Env proxy vars, TUN interfaces |
| 16 | Interface Stats | /proc/net/dev, error counters |
| 17 | System Logs | journalctl NM + wpa_supplicant, dmesg |
| 18 | Auth & Certs | wpa_supplicant, SSL certs, 802.1X config |
| 19 | Advanced Tests | HTTPS/HTTP curl, dig, NTP, traceroute — **slow** |

## Requirements

- Python 3.6+, no pip dependencies (stdlib only)
- Optional tools (gracefully skipped if missing): `iw`, `iwconfig`, `nmcli`, `lshw`, `dig`, `traceroute`, `speedtest-cli`, `mtr`
- Docker CLI (for disk cleaner Docker features)
