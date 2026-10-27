#!/usr/bin/env python3
"""
System Doctor — Linux System Toolkit
Main launcher with TUI menu.

Tools:
  1. Disk Cleaner         — find big files, Docker images/containers; delete interactively
  2. Network Diagnostics  — 19 network checks with two-panel TUI
  3. Bluetooth Diagnostics — 15 bluetooth checks with two-panel TUI (Ubuntu/Mint)
  4. WiFi Live Graph      — live chart of the WiFi download rate, red when stuck, w to reconnect
"""

import curses
import signal
import subprocess
import sys
import os

# Make sibling modules importable
sys.path.insert(0, os.path.dirname(__file__))

WIFI_GRAPH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wifi_graph.sh')


MENU_ITEMS = [
    {
        'key': '1',
        'title': 'Disk Cleaner',
        'desc': [
            'Find large files, directories, Docker images',
            'and containers. Select items and delete them',
            'interactively. Supports sudo for full access.',
        ],
        'hint': 'python3 disk_cleaner.py -h  for options',
    },
    {
        'key': '2',
        'title': 'Network Diagnostics',
        'desc': [
            'Run 19 network checks: WiFi signal, DNS, routing,',
            'connectivity, latency, bandwidth, firewall, and more.',
            'Two-panel TUI with per-check results and report export.',
        ],
        'hint': 'python3 net_diagnostics.py --run-all  to auto-run all',
    },
    {
        'key': '3',
        'title': 'Bluetooth Diagnostics',
        'desc': [
            'Run 15 bluetooth checks: hardware, kernel modules,',
            'service, rfkill, paired devices, audio, HID, power,',
            'logs, and more. Ubuntu and Linux Mint compatible.',
        ],
        'hint': 'python3 bt_diagnostics.py --run-all  to auto-run all',
    },
    {
        'key': '4',
        'title': 'WiFi Live Graph',
        'desc': [
            'Live chart of the WiFi download rate (AP -> PC),',
            'one column every 5 s. Red = stuck at 6 Mbit/s:',
            'press w to reconnect, Ctrl+C to come back here.',
        ],
        'hint': './wifi_graph.sh --selftest  to check the renderer',
    },
]

LOGO = [
    r"  ____            _                  ____             _             ",
    r" / ___| _   _ ___| |_ ___ _ __ ___  |  _ \  ___  ___| |_ ___  _ __ ",
    r" \___ \| | | / __| __/ _ \ '_ ` _ \ | | | |/ _ \/ __| __/ _ \| '__|",
    r"  ___) | |_| \__ \ ||  __/ | | | | || |_| | (_) | (__| || (_) | |   ",
    r" |____/ \__, |___/\__\___|_| |_| |_||____/ \___/ \___|\__\___/|_|   ",
    r"        |___/                                                         ",
]


def show_menu(stdscr):
    """Curses menu. Returns the chosen tool number (1-4), or None."""
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    cursor = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        row = 1

        # Logo
        for logo_line in LOGO:
            if row < h - 2:
                try:
                    stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                    stdscr.addstr(row, max(0, (w - len(logo_line)) // 2), logo_line[:w - 1])
                    stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
                except curses.error:
                    pass
            row += 1

        row += 1  # blank line after logo

        # Menu items
        for idx, item in enumerate(MENU_ITEMS):
            is_sel = (idx == cursor)
            if row >= h - 2:
                break

            # Item title
            prefix = f'  [{item["key"]}]  '
            title_line = prefix + item['title']
            try:
                if is_sel:
                    stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                    stdscr.addstr(row, 0, title_line[:w - 1].ljust(min(w - 1, 60)))
                    stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                    stdscr.addstr(row, 0, title_line[:w - 1])
                    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
            except curses.error:
                pass
            row += 1

            # Description lines
            indent = ' ' * (len(prefix) + 2)
            for desc_line in item['desc']:
                if row >= h - 2:
                    break
                try:
                    if is_sel:
                        stdscr.attron(curses.color_pair(3))
                    stdscr.addstr(row, 0, (indent + desc_line)[:w - 1])
                    if is_sel:
                        stdscr.attroff(curses.color_pair(3))
                except curses.error:
                    pass
                row += 1

            # Hint
            if row < h - 2:
                try:
                    stdscr.attron(curses.color_pair(1))
                    stdscr.addstr(row, 0, (indent + '$ ' + item['hint'])[:w - 1])
                    stdscr.attroff(curses.color_pair(1))
                except curses.error:
                    pass
                row += 1

            row += 1  # blank between items

        # Footer
        footer = ' ↑↓:Select  Enter:Launch  1-4:Quick launch  Q:Quit'
        try:
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(h - 1, 0, footer[:w - 1].ljust(w - 1))
            stdscr.attroff(curses.color_pair(4))
        except curses.error:
            pass

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord('q'), ord('Q'), 27):
            return None
        elif key == curses.KEY_UP:
            cursor = (cursor - 1) % len(MENU_ITEMS)
        elif key == curses.KEY_DOWN:
            cursor = (cursor + 1) % len(MENU_ITEMS)
        elif key in (ord('\n'), ord('\r'), curses.KEY_ENTER):
            return cursor + 1
        elif key == ord('1'):
            return 1
        elif key == ord('2'):
            return 2
        elif key == ord('3'):
            return 3
        elif key == ord('4'):
            return 4


def launch_disk_cleaner():
    from disk_cleaner import run_tui
    import argparse

    print('\n' + '─' * 60)
    print('  DISK CLEANER')
    print('─' * 60)
    path = input('  Search path [~]: ').strip() or '~'
    size_str = input('  Min size in MB [100]: ').strip() or '100'
    dirs_str = input('  Search directories instead of files? [y/N]: ').strip().lower()
    docker_str = input('  Skip Docker scan? [y/N]: ').strip().lower()
    print('─' * 60)

    try:
        min_size = float(size_str)
    except ValueError:
        min_size = 100

    run_tui(
        path=path,
        min_size_mb=min_size,
        search_dirs=(dirs_str in ('y', 'yes')),
        no_docker=(docker_str in ('y', 'yes')),
    )


def launch_net_diagnostics():
    from net_diagnostics import NetDiagnosticsTUI
    tui = NetDiagnosticsTUI()
    tui.run()


def launch_bt_diagnostics():
    from bt_diagnostics import BTDiagnosticsTUI
    tui = BTDiagnosticsTUI()
    tui.run()


def launch_wifi_graph():
    # Ctrl+C belongs to the graph, which restores the terminal and stops its monitor. The launcher
    # ignores it meanwhile so it returns to the menu instead of exiting. The child is spawned first:
    # an ignored SIGINT would be inherited and the graph could not be quit.
    graph = subprocess.Popen([WIFI_GRAPH])
    previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        graph.wait()
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def main():
    while True:
        choice = curses.wrapper(show_menu)
        if choice is None:
            break
        elif choice == 1:
            launch_disk_cleaner()
        elif choice == 2:
            launch_net_diagnostics()
        elif choice == 3:
            launch_bt_diagnostics()
        elif choice == 4:
            launch_wifi_graph()


if __name__ == '__main__':
    main()
