#!/usr/bin/env python3
"""
Risk classification for Disk Cleaner.

Answers one question per item: how much damage does deleting this do?
Pure string work — no filesystem access — so it stays free inside a full-home
walk and is trivially testable. Run this file directly for its self-check.
"""

import os


# ─── Risk levels ─────────────────────────────────────────────────────────────

CRITICAL = 'CRITICAL'
CAUTION = 'CAUTION'
UNKNOWN = 'UNKNOWN'
SAFE = 'SAFE'

# Most severe first — the confirm prompt reports in this order.
LEVELS = (CRITICAL, CAUTION, UNKNOWN, SAFE)

# A three-cell meter: more filled cells means more damage. UNKNOWN deliberately
# outranks SAFE — "could not identify this" is riskier than "this regenerates".
BADGES = {CRITICAL: '███', CAUTION: '██░', UNKNOWN: '█░░', SAFE: '░░░'}

# Shown in the confirmation prompt when a level is present in the selection.
CONSEQUENCE = {
    'git-history': 'repository history — unrecoverable once the pack is gone',
    'db-live':     'datafiles of a live database — deleting corrupts it',
    'media':       'user-uploaded originals — usually no second copy',
    'dump':        'a database dump — may be the only backup you have',
    'venv':        'a virtualenv — rebuild with pip install',
    'node_mod':    'installed packages — rebuild with npm install',
    'cache':       'a cache — regenerates on next use',
    'docker-img':  'a docker image — rebuild or re-pull to get it back',
    'docker-ctr':  'a docker container — a running one is doing work right now',
    'other':       'unclassified — the cleaner cannot tell what this is',
}


# ─── Path predicates ─────────────────────────────────────────────────────────
# Tested against path SEGMENTS, never substrings, so that 'mygit.tar' does not
# match the .git rule and 'mediaset.zip' does not match the media rule.

GIT_DIR = '.git'
DB_DIR_PREFIX = 'db_data'
DB_FILE_PREFIX = 'ib_logfile'
DB_FILE_SUFFIXES = ('.ibd', '.frm')
MEDIA_DIRS = frozenset({'media', 'uploads'})
DUMP_SUFFIXES = ('.sql', '.sql.gz', '.dump', '.bak')
VENV_DIRS = frozenset({'.venv', 'venv', 'site-packages'})
NODE_DIR = 'node_modules'
CACHE_DIRS = frozenset({'.cache', '__pycache__'})

# (category, level, predicate) — FIRST MATCH WINS, so order is meaningful.
RULES = (
    ('git-history', CRITICAL, lambda segs, name: GIT_DIR in segs),
    ('db-live', CRITICAL, lambda segs, name: (
        any(s.startswith(DB_DIR_PREFIX) for s in segs)
        or name.startswith(DB_FILE_PREFIX)
        or name.endswith(DB_FILE_SUFFIXES))),
    ('media', CAUTION, lambda segs, name: bool(MEDIA_DIRS.intersection(segs))),
    ('dump', CAUTION, lambda segs, name: name.endswith(DUMP_SUFFIXES)),
    ('venv', SAFE, lambda segs, name: bool(VENV_DIRS.intersection(segs))),
    ('node_mod', SAFE, lambda segs, name: NODE_DIR in segs),
    ('cache', SAFE, lambda segs, name: bool(CACHE_DIRS.intersection(segs))
        or any(s.startswith('.') and 'cache' in s for s in segs)),
)


def classify(path):
    """Return (category, level) for a filesystem path. Unmatched is UNKNOWN, never SAFE."""
    segs = path.split(os.sep)
    name = segs[-1] if segs else ''
    for category, level, matches in RULES:
        if matches(segs, name):
            return category, level
    return 'other', UNKNOWN


def classify_docker_image(container_count):
    """
    A docker image is a reproducible artifact — the risk is not the image, it is
    the container depending on it. So reference count is the whole signal.
    """
    return 'docker-img', (CAUTION if container_count else SAFE)


def classify_docker_container(is_running):
    return 'docker-ctr', (CAUTION if is_running else SAFE)


# ─── Path shortening ─────────────────────────────────────────────────────────

ELLIPSIS = '…'
# A first segment this short is a workspace container ('labs', 'app', 'src'),
# not the project itself — keep the next one too or the row loses its identity.
SHORT_SEGMENT_CHARS = 6
MAX_FILENAME_CHARS = 28
FILENAME_STEM_CHARS = 18
MIN_SEGMENTS_TO_ELIDE = 3


def _squeeze_filename(name):
    """Shrink a long hash-like filename, keeping the extension readable."""
    if len(name) <= MAX_FILENAME_CHARS:
        return name
    stem, dot, ext = name.rpartition('.')
    if dot:
        return stem[:FILENAME_STEM_CHARS] + ELLIPSIS + dot + ext
    return name[:MAX_FILENAME_CHARS - 2] + ELLIPSIS


def shorten(path, root, budget):
    """
    Fit a path into `budget` columns while keeping the two things that identify
    it: which project it belongs to, and the filename. Elides the middle — the
    old left-truncation ate the project name and left the useless tail.
    """
    if budget <= 0:
        return ''
    rel = os.path.relpath(path, root) if root else path
    if len(rel) <= budget:
        return rel

    segs = rel.split(os.sep)
    if len(segs) >= MIN_SEGMENTS_TO_ELIDE:
        keep = 2 if len(segs[0]) <= SHORT_SEGMENT_CHARS else 1
        head = os.sep.join(segs[:keep])
        candidate = f"{head}{os.sep}{ELLIPSIS}{os.sep}{_squeeze_filename(segs[-1])}"
        if len(candidate) <= budget:
            return candidate
    return ELLIPSIS + rel[-(budget - 1):]


# ─── Self-check ──────────────────────────────────────────────────────────────

def _self_check():
    incident = '/home/utente/lavoro/oknoplast-2/.git/objects/pack/pack-bb29450d7f3c1e4a.pack'
    assert classify(incident) == ('git-history', CRITICAL), 'the 2026-09-08 pack must read CRITICAL'
    assert classify('/srv/marbec/db_data_dev/wp_usermeta.ibd')[1] == CRITICAL
    assert classify('/srv/app/db_data_x/ib_logfile0')[1] == CRITICAL
    assert classify('/srv/app/media/uploads/photo.raw') == ('media', CAUTION)
    assert classify('/home/u/sixtus/sixtus-db_2026-08-17.sql') == ('dump', CAUTION)
    assert classify('/p/.venv/lib/python3.12/site-packages/x.so')[1] == SAFE
    assert classify('/p/node_modules/big.bin') == ('node_mod', SAFE)
    assert classify('/p/.minis-cache/app.apk') == ('cache', SAFE)

    # Segment tests, not substring tests.
    assert classify('/home/u/backups/mygit.tar') == ('other', UNKNOWN), 'mygit.tar is not git'
    assert classify('/home/u/dl/mediaset.zip') == ('other', UNKNOWN), 'mediaset.zip is not media'
    assert classify('/home/u/random.bin') == ('other', UNKNOWN), 'default must be UNKNOWN, not SAFE'

    assert classify_docker_image(0)[1] == SAFE
    assert classify_docker_image(3)[1] == CAUTION, 'an image a container depends on is not SAFE'
    assert classify_docker_container(is_running=True)[1] == CAUTION
    assert classify_docker_container(is_running=False)[1] == SAFE

    root = '/home/utente/lavoro'
    assert shorten(incident, root, 44) == 'oknoplast-2/…/pack-bb29450d7f3c1e4a.pack'
    # A short first segment is a container, not the project — keep two.
    nested = shorten('/home/utente/lavoro/labs/img2alt/.venv/lib/x/libcusolver.so.11', root, 40)
    assert nested.startswith('labs/img2alt/'), nested
    assert shorten(incident, root, 500) == \
        'oknoplast-2/.git/objects/pack/pack-bb29450d7f3c1e4a.pack', 'no elision when it fits'
    assert len(shorten(incident, root, 20)) <= 20

    # UNKNOWN must sort as riskier than SAFE.
    assert LEVELS.index(UNKNOWN) < LEVELS.index(SAFE)
    print('risk.py: all checks passed')


if __name__ == '__main__':
    _self_check()
