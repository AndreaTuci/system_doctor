#!/usr/bin/env python3
"""
Runnable checks for the non-obvious logic in disk_cleaner.py: the two docker
time formats and the selection set algebra. Run it directly.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import risk
from disk_cleaner import _epoch, _ago, _build_image_item, InteractivePicker

NOW = 1_800_000_000
DAY = 86400


def check_epoch():
    # The same instant in both formats docker emits must parse identically:
    # 16:37:59 at +02:00 is 14:37:59 UTC.
    assert _epoch('2026-09-11 16:37:59 +0200 CEST') == _epoch('2026-09-11T14:37:59Z')
    # Nanosecond precision must not defeat the parser.
    assert _epoch('2026-09-15T08:15:20.884341427Z') > _epoch('2025-10-07T10:23:47Z')
    # Docker's zero value for a container that never ran.
    assert _epoch('0001-01-01T00:00:00Z') == 0
    assert _epoch('') == 0 and _epoch(None) == 0 and _epoch('nonsense') == 0


def check_ago():
    assert _ago(NOW, NOW) == 'today'
    assert _ago(NOW - 5 * DAY, NOW) == '5d'
    assert _ago(NOW - 60 * DAY, NOW) == '2mo'
    assert _ago(NOW - 800 * DAY, NOW) == '2y'
    assert _ago(0, NOW) == '?', 'an unknown time must not read as recent'


def check_image_staleness():
    usage = {'aaaaaaaaaaaa': {'unique': 10, 'shared': 0, 'containers': 0},
             'bbbbbbbbbbbb': {'unique': 20, 'shared': 0, 'containers': 1}}
    built_old = {'ID': 'aaaaaaaaaaaa', 'Repository': 'old', 'Tag': 'latest',
                 'CreatedAt': '2020-01-01 10:00:00 +0100 CET'}
    run_today = {'ID': 'bbbbbbbbbbbb', 'Repository': 'hot', 'Tag': 'latest',
                 'CreatedAt': '2020-01-01 10:00:00 +0100 CET'}
    activity = {'bbbbbbbbbbbb': NOW}

    old = _build_image_item(built_old, usage, {}, activity, NOW)
    hot = _build_image_item(run_today, usage, {}, activity, NOW)

    assert old['extra'].startswith('built '), old['extra']
    assert hot['extra'] == 'run today', hot['extra']
    assert old['used_at'] < hot['used_at'], 'never-run must sort above recently-run'
    assert old['level'] == risk.SAFE and hot['level'] == risk.CAUTION

    # An untagged image with no compose label must still say what it is.
    unknown = _build_image_item({'ID': 'cccccccccccc', 'Repository': '<none>', 'Tag': '<none>',
                                 'CreatedAt': ''}, {}, {}, {}, NOW)
    assert 'origin unknown' in unknown['name'], unknown['name']
    assert unknown['status'] == 'SIZE?', 'no df -v data must be admitted, not faked'


def check_level_selection():
    def item(path):
        category, level = risk.classify(path)
        return {'type': 'file', 'path': path, 'name': os.path.basename(path),
                'size': 1, 'status': '', 'category': category, 'level': level}

    pack = '/w/proj/.git/objects/pack/p.pack'
    cache = '/w/proj/.cache/big.bin'
    picker = InteractivePicker([item(pack), item(cache)], [], [], scan_root='/w')

    picker._toggle_level(risk.CRITICAL)
    assert picker.selected_ids == {pack}, picker.selected_ids
    picker._toggle_level(risk.CRITICAL)          # toggling again clears it
    assert picker.selected_ids == set()

    picker._toggle_level(risk.SAFE)
    assert picker.selected_ids == {cache}
    picker._toggle_level(risk.CAUTION)           # nothing at that level: no change
    assert picker.selected_ids == {cache}

    picker._select_all_toggle()
    assert picker.selected_ids == {pack, cache}, 'partial selection must extend to all'


if __name__ == '__main__':
    for check in (check_epoch, check_ago, check_image_staleness, check_level_selection):
        check()
    print('test_disk_cleaner.py: all checks passed')
