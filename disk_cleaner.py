#!/usr/bin/env python3
"""
Disk Cleaner - part of System Doctor.
Find big files/dirs and Docker resources. Select and delete interactively.
Run with sudo for full filesystem access.
"""

import os
import argparse
import curses
import subprocess
import json
import shutil
import sys

import risk


# Colour pair per risk level, initialised in _curses_main.
LEVEL_COLORS = {risk.CRITICAL: 4, risk.CAUTION: 8, risk.UNKNOWN: 9, risk.SAFE: 2}

CATEGORY_COLUMN_CHARS = 12
MIN_NAME_CHARS = 30              # the name outranks every trailing badge
# Measured: at 76 columns the category still leaves 41 for an elided path, which
# fits 'project/…/file.ext'. Below that the column goes and the badge colour carries it.
CATEGORY_MIN_WIDTH = 76


def _row_suffix(item, budget=None):
    """
    Trailing badges, most important first: status, the shared-layer caveat, age.
    When `budget` columns is given, drop from the tail rather than let the suffix
    squeeze the name — a truncated name is what caused the 2026-09-08 deletion.
    """
    parts = []
    if item.get('status'):
        parts.append(f"[{item['status']}]")
    if item.get('shared'):
        parts.append(f"+{format_size(item['shared']).strip()} shared")
    if item.get('extra') and item['type'] == 'docker_image':
        parts.append(item['extra'])
    if budget is not None:
        while parts and len('  ' + '  '.join(parts)) > budget:
            parts.pop()
    return ('  ' + '  '.join(parts)) if parts else ''


# ─── Formatting ──────────────────────────────────────────────────────────────

def format_size(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:7.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


# ─── File scanning ────────────────────────────────────────────────────────────

def get_dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += get_dir_size(entry.path)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total


def find_large_items(root_path, min_size_mb, search_dirs=False):
    min_size_bytes = min_size_mb * 1024 * 1024
    results = []
    try:
        for root, dirs, files in os.walk(root_path):
            if not search_dirs:
                for file in files:
                    try:
                        filepath = os.path.join(root, file)
                        size = os.path.getsize(filepath)
                        if size >= min_size_bytes:
                            abspath = os.path.abspath(filepath)
                            category, level = risk.classify(abspath)
                            results.append({
                                'type': 'dir' if os.path.isdir(filepath) else 'file',
                                'path': abspath,
                                'name': file,
                                'size': size,
                                'status': '',
                                'category': category,
                                'level': level,
                            })
                    except (PermissionError, OSError, FileNotFoundError):
                        continue
            else:
                for dir_name in dirs:
                    try:
                        dirpath = os.path.join(root, dir_name)
                        size = get_dir_size(dirpath)
                        if size >= min_size_bytes:
                            abspath = os.path.abspath(dirpath)
                            category, level = risk.classify(abspath)
                            results.append({
                                'type': 'dir',
                                'path': abspath,
                                'name': dir_name,
                                'size': size,
                                'status': '',
                                'category': category,
                                'level': level,
                            })
                    except (PermissionError, OSError):
                        continue
    except (PermissionError, OSError):
        pass
    results.sort(key=lambda x: x['size'], reverse=True)
    return results


# ─── Docker scanning ─────────────────────────────────────────────────────────

def _parse_docker_size(s):
    """Parse Docker size string like '1.2GB', '800MB' or '3.246kB' to bytes."""
    if not s:
        return 0
    s = str(s).strip()
    lowered = s.lower()
    for suffix, mult in [('TiB', 1024**4), ('GiB', 1024**3), ('MiB', 1024**2), ('KiB', 1024),
                          ('TB', 1024**4), ('GB', 1024**3), ('MB', 1024**2), ('KB', 1024), ('B', 1)]:
        # Docker mixes cases across subcommands — 'MB' from `images`, 'kB' from `system df`.
        if lowered.endswith(suffix.lower()):
            try:
                return int(float(s[:-len(suffix)]) * mult)
            except ValueError:
                return 0
    return 0


def _run_docker(*args, timeout=15):
    """Run a docker command, return (returncode, stdout). Returns (-1, '') if docker missing."""
    try:
        r = subprocess.run(['docker'] + list(args), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except FileNotFoundError:
        return -1, ''
    except subprocess.TimeoutExpired:
        return -1, ''


IMAGE_ID_CHARS = 12
DOCKER_NO_VALUE = '<no value>'          # what docker prints for a missing label
UNTAGGED = '<none>:<none>'
DF_TIMEOUT = 60                          # `system df -v` walks the layer store
COMPOSE_LABEL_FORMAT = (
    '{{.Id}}|{{index .Config.Labels "com.docker.compose.project"}}'
    '|{{index .Config.Labels "com.docker.compose.service"}}'
)


def _short_image_id(raw):
    return str(raw or '').replace('sha256:', '')[:IMAGE_ID_CHARS]


def _docker_disk_usage():
    """
    Real per-image sizes from ONE `docker system df -v` call.

    `docker images` reports a size that double-counts layers shared between
    images: four 812 MB images sharing one base free 812 MB in total, not 3.2 GB.
    UniqueSize is what deleting that single image actually reclaims. The same
    call reports how many containers reference the image, which is a far more
    reliable USED/UNUSED signal than matching name strings.

    Returns {image_id: {'unique', 'shared', 'containers'}}, empty when unavailable.
    """
    rc, out = _run_docker('system', 'df', '-v', '--format', '{{json .}}', timeout=DF_TIMEOUT)
    if rc != 0:
        return {}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {}

    usage = {}
    for img in payload.get('Images') or []:
        image_id = _short_image_id(img.get('ID'))
        if not image_id:
            continue
        try:
            containers = int(str(img.get('Containers', '0')))
        except ValueError:
            containers = 0
        usage[image_id] = {
            'unique': _parse_docker_size(img.get('UniqueSize')),
            'shared': _parse_docker_size(img.get('SharedSize')),
            'containers': containers,
        }
    return usage


def _docker_compose_labels(image_ids):
    """
    Resolve compose project/service for untagged images in ONE batched inspect.

    `docker image inspect` accepts every id at once, so this costs a single call
    (~0.05 s for 29 images) rather than one spawn per image. Note that
    `docker images --format '{{.Label ...}}'` does NOT work — the images
    formatter has no Label field.
    """
    if not image_ids:
        return {}
    rc, out = _run_docker('image', 'inspect', *image_ids, '--format', COMPOSE_LABEL_FORMAT)
    if rc != 0:
        return {}

    names = {}
    for line in out.strip().splitlines():
        parts = line.split('|')
        if len(parts) != 3:
            continue
        image_id, project, service = _short_image_id(parts[0]), parts[1], parts[2]
        if not image_id or project == DOCKER_NO_VALUE:
            continue
        names[image_id] = f"{project}/{service}" if service != DOCKER_NO_VALUE else project
    return names


def _dangling_image_ids():
    rc, out = _run_docker('images', '-f', 'dangling=true', '-q')
    if rc != 0:
        return set()
    return {_short_image_id(line) for line in out.splitlines() if line.strip()}


def _build_image_item(img, usage, compose_names):
    """One image row: compose-resolved name, reclaimable size, reference count, risk."""
    image_id = _short_image_id(img.get('ID'))
    full_name = f"{img.get('Repository', '<none>')}:{img.get('Tag', '<none>')}"
    if full_name == UNTAGGED:
        origin = compose_names.get(image_id)
        full_name = f"{origin}  (dangling)" if origin else 'untagged  (origin unknown)'

    stats = usage.get(image_id)
    if stats is None:
        # No `system df -v` means no honest size and no reference count — say so
        # rather than quietly presenting the double-counted number as truth.
        reclaimable, shared, status = _parse_docker_size(img.get('Size', '0B')), 0, 'SIZE?'
        category, level = 'docker-img', risk.UNKNOWN
    else:
        reclaimable, shared = stats['unique'], stats['shared']
        status = 'USED' if stats['containers'] else 'UNUSED'
        category, level = risk.classify_docker_image(stats['containers'])

    return {
        'type': 'docker_image',
        'name': full_name,
        'id': image_id,
        'path': image_id,
        'size': reclaimable,
        'shared': shared,
        'status': status,
        'extra': img.get('CreatedSince', ''),
        'category': category,
        'level': level,
    }


def get_docker_images():
    """Return docker image dicts, with untagged images resolved to their compose origin."""
    rc, out = _run_docker('images', '--format', '{{json .}}')
    if rc != 0:
        return []

    dangling = _dangling_image_ids()
    usage = _docker_disk_usage()
    compose_names = _docker_compose_labels(sorted(dangling))

    images = []
    for line in out.strip().splitlines():
        if not line:
            continue
        try:
            img = json.loads(line)
        except json.JSONDecodeError:
            continue

        images.append(_build_image_item(img, usage, compose_names))

    images.sort(key=lambda x: x['size'], reverse=True)
    return images


def get_docker_containers():
    """Return list of docker container dicts with RUNNING/STOPPED status."""
    rc, out = _run_docker('ps', '-a', '-s', '--format', '{{json .}}')
    if rc != 0:
        return []

    containers = []
    for line in out.strip().splitlines():
        if not line:
            continue
        try:
            c = json.loads(line)
            name = c.get('Names', 'unknown')
            cid = c.get('ID', '')[:12]
            status = c.get('Status', 'unknown')
            image = c.get('Image', 'unknown')
            size_str = c.get('Size', '0B')
            size_bytes = _parse_docker_size(size_str.split('(')[0].strip())
            is_running = status.lower().startswith('up')

            category, level = risk.classify_docker_container(is_running)
            containers.append({
                'type': 'docker_container',
                'name': f"{name}  [{image}]",
                'id': cid,
                'path': cid,
                'size': size_bytes,
                'status': 'RUNNING' if is_running else 'STOPPED',
                'is_running': is_running,
                'extra': status,
                'category': category,
                'level': level,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    containers.sort(key=lambda x: x['size'], reverse=True)
    return containers


# ─── Deletion ────────────────────────────────────────────────────────────────

def delete_item(item):
    """Delete item. Returns (success: bool, message: str)."""
    try:
        t = item['type']
        if t == 'file':
            os.remove(item['path'])
            return True, f"Deleted: {item['path']}"
        elif t == 'dir':
            shutil.rmtree(item['path'])
            return True, f"Deleted dir: {item['path']}"
        elif t == 'docker_image':
            r = subprocess.run(['docker', 'rmi', '-f', item['id']],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return True, f"Removed image: {item['name']}"
            return False, f"rmi failed: {r.stderr.strip()}"
        elif t == 'docker_container':
            r = subprocess.run(['docker', 'rm', '-f', item['id']],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return True, f"Removed container: {item['name']}"
            return False, f"rm failed: {r.stderr.strip()}"
    except (PermissionError, OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return False, "Unknown type"


# ─── Interactive TUI ─────────────────────────────────────────────────────────

class InteractivePicker:
    def __init__(self, file_items, docker_images, docker_containers, scan_root=''):
        # Paths are shortened relative to the scan root, so the project name —
        # the part that identifies a file — survives instead of being truncated.
        self.scan_root = scan_root
        self.file_items = list(file_items)
        self.docker_images = list(docker_images)
        self.docker_containers = list(docker_containers)
        self.selected_ids = set()
        self.cursor = 0
        self.scroll_offset = 0
        self.status_msg = ''
        self.status_is_error = False
        self.rows = []
        self.id_to_item = {}
        self._build_rows()

    def _build_rows(self):
        self.rows = []
        self.id_to_item = {}

        def add_section(label, items):
            if not items:
                return
            self.rows.append({'type': 'header', 'label': label})
            for item in items:
                uid = item.get('path') or item.get('id', id(item))
                item['_uid'] = uid
                self.id_to_item[uid] = item
                self.rows.append({'type': 'item', 'item': item, 'uid': uid})

        add_section('FILES & DIRECTORIES', self.file_items)
        add_section('DOCKER IMAGES', self.docker_images)
        add_section('DOCKER CONTAINERS', self.docker_containers)

        if not self.rows:
            self.rows.append({'type': 'empty'})

        self.cursor = next((i for i, r in enumerate(self.rows) if r['type'] == 'item'), 0)
        valid = set(self.id_to_item.keys())
        self.selected_ids &= valid

    def _navigable(self):
        return [i for i, r in enumerate(self.rows) if r['type'] == 'item']

    def _move_cursor(self, delta):
        nav = self._navigable()
        if not nav:
            return
        if self.cursor not in nav:
            self.cursor = nav[0]
            return
        pos = nav.index(self.cursor)
        self.cursor = nav[max(0, min(pos + delta, len(nav) - 1))]

    def _toggle_current(self):
        row = self.rows[self.cursor]
        if row['type'] != 'item':
            return
        uid = row['uid']
        if uid in self.selected_ids:
            self.selected_ids.discard(uid)
        else:
            self.selected_ids.add(uid)

    def _select_all_toggle(self):
        all_uids = {r['uid'] for r in self.rows if r['type'] == 'item'}
        if all_uids.issubset(self.selected_ids):
            self.selected_ids -= all_uids
        else:
            self.selected_ids |= all_uids

    def _select_unused_stopped(self):
        for row in self.rows:
            if row['type'] == 'item' and row['item'].get('status') in ('UNUSED', 'STOPPED'):
                self.selected_ids.add(row['uid'])

    def _get_selected_items(self):
        return [self.id_to_item[uid] for uid in self.selected_ids if uid in self.id_to_item]

    def _breakdown(self, items):
        """Selected items grouped by risk level, most severe first."""
        groups = []
        for level in risk.LEVELS:
            at_level = [i for i in items if i.get('level') == level]
            if at_level:
                groups.append((level, at_level))
        return groups

    def _confirm_lines(self, items, groups):
        """(level, text) rows naming what the selection is made of, severest first."""
        total = sum(i['size'] for i in items)
        lines = [(None, f" Delete {len(items)} item(s) · {format_size(total)} ?")]
        for level, at_level in groups:
            sample = at_level[0].get('category', 'other')
            lines.append((level, f"   {risk.BADGES[level]} {level:<9}{len(at_level):>3}  "
                                 f"{sample:<12} {risk.CONSEQUENCE.get(sample, '')}"))
        return lines

    def _confirm_deletion(self, stdscr, items):
        """
        Show what the selection is made of, then ask. Never refuses — but when
        something CRITICAL is selected the word you must type names the damage,
        so the confirmation cannot be muscle-memory.
        """
        h, w = stdscr.getmaxyx()
        groups = self._breakdown(items)
        riskiest = groups[0][0] if groups else risk.UNKNOWN
        lines = self._confirm_lines(items, groups)
        needs_word = riskiest == risk.CRITICAL
        prompt = (f" Type {risk.CRITICAL} to confirm, or anything else to cancel: "
                  if needs_word else " Confirm? [y/N]: ")
        lines.append((riskiest if needs_word else None, prompt))

        first_row = max(0, h - 1 - len(lines))
        for offset, (level, text) in enumerate(lines):
            attr = curses.color_pair(LEVEL_COLORS[level]) | curses.A_BOLD if level else curses.A_BOLD
            try:
                stdscr.attron(attr)
                stdscr.addstr(first_row + offset, 0, text[:w - 1].ljust(w - 1))
                stdscr.attroff(attr)
            except curses.error:
                pass
        stdscr.refresh()

        if not needs_word:
            return stdscr.getch() in (ord('y'), ord('Y'))
        curses.echo()
        curses.curs_set(1)
        try:
            typed = stdscr.getstr(first_row + len(lines) - 1, min(len(prompt), w - 2), 16)
        except curses.error:
            typed = b''
        finally:
            curses.noecho()
            curses.curs_set(0)
        return typed.decode('utf-8', 'replace').strip() == risk.CRITICAL

    def _delete_selected(self, stdscr):
        items = self._get_selected_items()
        if not items:
            self.status_msg = "Nothing selected — press SPACE to select items."
            self.status_is_error = True
            return

        if not self._confirm_deletion(stdscr, items):
            self.status_msg = "Cancelled."
            self.status_is_error = False
            return

        deleted, errors, freed = [], [], 0
        for item in items:
            ok, msg = delete_item(item)
            if ok:
                deleted.append(item)
                freed += item['size']
                t = item['type']
                if t in ('file', 'dir') and item in self.file_items:
                    self.file_items.remove(item)
                elif t == 'docker_image' and item in self.docker_images:
                    self.docker_images.remove(item)
                elif t == 'docker_container' and item in self.docker_containers:
                    self.docker_containers.remove(item)
            else:
                errors.append(msg)

        self._build_rows()

        if errors:
            self.status_msg = f"Deleted {len(deleted)}, {len(errors)} error(s): {errors[0][:70]}"
            self.status_is_error = True
        else:
            self.status_msg = f"Deleted {len(deleted)} item(s), freed {format_size(freed)}"
            self.status_is_error = False

    def run(self):
        curses.wrapper(self._curses_main)

    def _curses_main(self, stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(6, curses.COLOR_GREEN, -1)
        curses.init_pair(8, curses.COLOR_YELLOW, -1)
        curses.init_pair(9, curses.COLOR_WHITE, -1)

        while True:
            self._draw(stdscr)
            key = stdscr.getch()

            if key in (ord('q'), ord('Q'), 27):
                break
            elif key == curses.KEY_UP:
                self._move_cursor(-1)
                self.status_msg = ''
            elif key == curses.KEY_DOWN:
                self._move_cursor(1)
                self.status_msg = ''
            elif key == curses.KEY_PPAGE:
                h, _ = stdscr.getmaxyx()
                self._move_cursor(-(h - 4))
            elif key == curses.KEY_NPAGE:
                h, _ = stdscr.getmaxyx()
                self._move_cursor(h - 4)
            elif key == ord(' '):
                self._toggle_current()
            elif key in (ord('d'), ord('D')):
                self._delete_selected(stdscr)
            elif key in (ord('a'), ord('A')):
                self._select_all_toggle()
            elif key in (ord('u'), ord('U')):
                self._select_unused_stopped()
            elif key in (ord('?'), ord('h'), ord('H')):
                self._show_help(stdscr)

    def _show_help(self, stdscr):
        h, w = stdscr.getmaxyx()
        lines = [
            "  KEYBOARD SHORTCUTS  ",
            "",
            "  ↑↓ PgUp/PgDn Navigate   SPACE Toggle   A Select all   U Unused+Stopped",
            "  D Delete (asks confirmation)   Q/Esc Quit   ?/H This help",
            "",
            "  RISK BADGE — what deleting this costs you",
            "  ███ CRITICAL  unrecoverable: git history, live DB datafiles",
            "  ██░ CAUTION   probably no second copy: uploads, DB dumps",
            "  █░░ UNKNOWN   not identified — the cleaner cannot vouch for it",
            "  ░░░ SAFE      regenerates: venv, node_modules, caches, images",
            "",
            "  Age never lowers a badge. A git pack from 2022 reads as cold",
            "  precisely because it is history — cold is not the same as safe.",
            "",
            "  [USED] / [UNUSED]  image referenced by a container, or not",
            "  +N MB shared       layers shared with other images — you reclaim",
            "                     that space only by deleting all of them",
            "  Press any key to close",
        ]
        box_w = min(max(len(l) for l in lines) + 4, w - 4)
        box_h = min(len(lines) + 2, h - 4)
        box_y = (h - box_h) // 2
        box_x = (w - box_w) // 2

        for i, text in enumerate(lines[:box_h - 2]):
            y = box_y + 1 + i
            padded = (' ' + text).ljust(box_w)[:box_w]
            try:
                if i == 0:
                    stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                    stdscr.addstr(y, box_x, padded)
                    stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.attron(curses.A_NORMAL)
                    stdscr.addstr(y, box_x, padded)
                    stdscr.attroff(curses.A_NORMAL)
            except curses.error:
                pass

        stdscr.refresh()
        stdscr.getch()

    def _item_line(self, item, is_sel, width):
        """
        One rendered row. The name gets its columns before any trailing badge:
        a path truncated past recognition is what made the 2026-09-08 deletion
        look safe.
        """
        level = item.get('level', risk.UNKNOWN)
        checkbox = '[x]' if is_sel else '[ ]'
        cat_col = (f"{item.get('category', 'other'):<{CATEGORY_COLUMN_CHARS}}"
                   if width >= CATEGORY_MIN_WIDTH else '')
        prefix = f"  {checkbox} {risk.BADGES[level]} {cat_col}{format_size(item['size'])}  "
        suffix = _row_suffix(item, max(0, width - len(prefix) - MIN_NAME_CHARS - 1))

        avail_name = max(10, width - len(prefix) - len(suffix) - 1)
        if item['type'] in ('file', 'dir'):
            name = risk.shorten(item['path'], self.scan_root, avail_name)
        else:
            name = item['name']
            if len(name) > avail_name:
                name = name[:avail_name - 1] + '…'
        return (prefix + name.ljust(avail_name) + suffix)[:width - 1]

    def _draw(self, stdscr):
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        sel_size = sum(self.id_to_item[uid]['size']
                       for uid in self.selected_ids if uid in self.id_to_item)
        title = " Big Files & Docker Cleaner "
        sel_info = f" {len(self.selected_ids)} selected ({format_size(sel_size)}) "
        pad = ' ' * max(0, w - len(title) - len(sel_info))
        title_line = (title + pad + sel_info)[:w]
        try:
            stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
            stdscr.addstr(0, 0, title_line)
            stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            pass

        list_y_start = 1
        list_y_end = h - 2
        visible = list_y_end - list_y_start

        if self.cursor < self.scroll_offset:
            self.scroll_offset = self.cursor
        elif self.cursor >= self.scroll_offset + visible:
            self.scroll_offset = self.cursor - visible + 1

        for row_i in range(visible):
            list_idx = row_i + self.scroll_offset
            if list_idx >= len(self.rows):
                break
            y = list_y_start + row_i
            row = self.rows[list_idx]

            try:
                if row['type'] == 'header':
                    label = row['label']
                    line = f"  {'─' * 3} {label} {'─' * max(0, w - len(label) - 8)}"
                    stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                    stdscr.addstr(y, 0, line[:w - 1])
                    stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

                elif row['type'] == 'empty':
                    stdscr.addstr(y, 0, "  No items found.")

                elif row['type'] == 'item':
                    item = row['item']
                    is_sel = row['uid'] in self.selected_ids
                    line = self._item_line(item, is_sel, w)

                    if list_idx == self.cursor:
                        attr = curses.color_pair(3) | curses.A_BOLD
                    elif is_sel:
                        attr = curses.color_pair(2) | curses.A_BOLD
                    else:
                        attr = curses.color_pair(LEVEL_COLORS[item.get('level', risk.UNKNOWN)])

                    stdscr.attron(attr)
                    stdscr.addstr(y, 0, line)
                    stdscr.attroff(attr)

            except curses.error:
                pass

        try:
            if self.status_msg:
                attr = (curses.color_pair(4) | curses.A_BOLD
                        if self.status_is_error else curses.color_pair(6) | curses.A_BOLD)
                stdscr.attron(attr)
                stdscr.addstr(h - 1, 0, (' ' + self.status_msg)[:w - 1].ljust(w - 1))
                stdscr.attroff(attr)
            else:
                footer = " ↑↓:Navigate  SPC:Select  A:All  U:Unused/Stopped  D:Delete  Q:Quit  ?:Help"
                stdscr.attron(curses.color_pair(5))
                stdscr.addstr(h - 1, 0, footer[:w - 1].ljust(w - 1))
                stdscr.attroff(curses.color_pair(5))
        except curses.error:
            pass

        stdscr.refresh()


# ─── Non-interactive output ───────────────────────────────────────────────────

def print_results(file_items, docker_images, docker_containers):
    def print_section(title, items, cols):
        if not items:
            return
        print(f"\n{'=' * 100}")
        print(f"  {title}")
        print(f"{'=' * 100}")
        for item in items:
            level = item.get('level', risk.UNKNOWN)
            parts = [risk.BADGES[level], f"{item.get('category', 'other'):<12}",
                     format_size(item['size'])]
            for col in cols:
                parts.append(str(item.get(col, '')))
            print('  '.join(parts) + _row_suffix(item))
        total = sum(i['size'] for i in items)
        print(f"  Total: {len(items)} items — {format_size(total)}")
        by_level = {lvl: sum(1 for i in items if i.get('level') == lvl) for lvl in risk.LEVELS}
        print('  Risk: ' + '  '.join(f"{risk.BADGES[lvl]} {lvl} {n}"
                                     for lvl, n in by_level.items() if n))

    print_section('FILES & DIRECTORIES', file_items, ['path'])
    print_section('DOCKER IMAGES', docker_images, ['name', 'id'])
    print_section('DOCKER CONTAINERS', docker_containers, ['name', 'id'])


# ─── TUI launcher (called by system_doctor) ───────────────────────────────────

def run_tui(path='.', min_size_mb=100, search_dirs=False, no_docker=False):
    """Launch the disk cleaner TUI. Called by system_doctor or standalone."""
    search_path = os.path.expanduser(path)

    file_items = []
    docker_images = []
    docker_containers = []

    if os.path.exists(search_path):
        kind = 'directories' if search_dirs else 'files'
        print(f'Scanning {kind} > {min_size_mb} MB in {os.path.abspath(search_path)} ...', flush=True)
        file_items = find_large_items(search_path, min_size_mb, search_dirs)
        print(f'  Found {len(file_items)} item(s).')

    if not no_docker:
        print('Scanning Docker images ...', flush=True)
        docker_images = get_docker_images()
        print(f'  Found {len(docker_images)} image(s).')
        print('Scanning Docker containers ...', flush=True)
        docker_containers = get_docker_containers()
        print(f'  Found {len(docker_containers)} container(s).')

    picker = InteractivePicker(file_items, docker_images, docker_containers,
                               scan_root=os.path.abspath(search_path))
    picker.run()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Find big files/dirs and Docker resources. Select and delete interactively.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                         # Files > 100 MB in current dir
  %(prog)s -s 500                  # Files > 500 MB
  %(prog)s -d                      # Directories > 100 MB
  %(prog)s -p /home -s 200         # Files > 200 MB in /home
  %(prog)s --no-docker             # Skip Docker scan
  %(prog)s --docker-only           # Only Docker resources (no file scan)
  %(prog)s --no-interactive        # Print results, no TUI
        """
    )
    parser.add_argument('-p', '--path', default='.', help='Search path (default: current dir)')
    parser.add_argument('-s', '--size', type=float, default=100, help='Min size in MB (default: 100)')
    parser.add_argument('-d', '--dirs', action='store_true', help='Search directories instead of files')
    parser.add_argument('--no-docker', action='store_true', help='Skip Docker scan')
    parser.add_argument('--docker-only', action='store_true', help='Only scan Docker resources')
    parser.add_argument('--no-interactive', action='store_true', help='Print results without TUI')

    args = parser.parse_args()
    search_path = os.path.expanduser(args.path)

    if not os.path.exists(search_path) and not args.docker_only:
        print(f"Error: path '{search_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    file_items = []
    docker_images = []
    docker_containers = []

    if not args.docker_only:
        kind = "directories" if args.dirs else "files"
        print(f"Scanning {kind} > {args.size} MB in {os.path.abspath(search_path)} ...", flush=True)
        file_items = find_large_items(search_path, args.size, args.dirs)
        print(f"  Found {len(file_items)} item(s).")

    if not args.no_docker:
        print("Scanning Docker images ...", flush=True)
        docker_images = get_docker_images()
        if docker_images == [] and _run_docker('info')[0] == -1:
            print("  Docker not available, skipping.")
        else:
            print(f"  Found {len(docker_images)} image(s).")

        print("Scanning Docker containers ...", flush=True)
        docker_containers = get_docker_containers()
        print(f"  Found {len(docker_containers)} container(s).")

    if args.no_interactive:
        print_results(file_items, docker_images, docker_containers)
    else:
        picker = InteractivePicker(file_items, docker_images, docker_containers,
                                   scan_root=os.path.abspath(search_path))
        picker.run()


if __name__ == '__main__':
    main()
