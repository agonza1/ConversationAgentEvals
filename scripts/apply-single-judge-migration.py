"""Temporary exact-source transport for PR #158; removed from the candidate tree.

Applies only reviewed declarative text edits, checks every input/output digest,
runs no downloaded code, and publishes a tree without changing any branch ref.
The Actions job runs API tests and the web build before --publish-tree.
Browser verification was blocked by an administrator policy and is not retried.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import subprocess
import urllib.request

REPOSITORY = 'agonza1/ConversationAgentEvals'
BRANCH = 'refactor/single-assert-judge-path'
CHUNKS = (
    '1d6211214630a180b0e6b5d72cd67a4aa17e8212',
    'ba038e42b7e958558594da71f54bd1a568f7c145',
    'bc60b8476b8da19110d0848c8771f8e157ed2a58',
    'e15f43f7e25ec7d52968956f501c0a14e05df810',
)
BUNDLE_SHA256 = '6ae1d8c58596ae84470be2adb7021cb44f832df604e665e7a903b3609938ec1e'
TRANSPORT_PATHS = (
    '.github/workflows/single-judge-migration.yml',
    'scripts/apply-single-judge-migration.py',
)
# The existing browser configuration is retained. Do not add alternate browser
# binaries, launch flags, hosts, or runners to work around the observed policy.
EXCLUDED = {'apps/web/playwright.config.ts'}
MANIFEST = Path('/tmp/cae-migration-manifest.json')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def api(path: str, data: dict | None = None) -> dict:
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'cae-pr-158-migration',
               'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if data is not None and not token:
        raise RuntimeError('Publishing requires the workflow-scoped token.')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{REPOSITORY}/{path}', headers=headers,
        data=json.dumps(data).encode() if data is not None else None,
        method='POST' if data is not None else 'GET',
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def checked_path(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not (
        value.startswith(('apps/', 'docs/', 'scripts/')) or value in {'README.md', '.env.example'}
    ):
        raise ValueError(f'Unexpected migration path: {value!r}')
    result = Path(value)
    if result.is_symlink():
        raise ValueError(f'Symlink target is not allowed: {value!r}')
    result.resolve().relative_to(Path.cwd().resolve())
    return result


def apply() -> None:
    source_sha = git('rev-parse', 'HEAD')
    parts = []
    for sha in CHUNKS:
        blob = api(f'git/blobs/{sha}')
        raw = base64.b64decode(blob['content'])
        git_sha = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
        if git_sha != sha:
            raise ValueError('Transport blob identity mismatch.')
        parts.append(raw.decode('ascii'))
    packed = base64.b64decode(''.join(parts), validate=True)
    if digest(packed) != BUNDLE_SHA256:
        raise ValueError('Migration bundle digest mismatch.')
    edits = json.loads(lzma.decompress(packed))
    if not isinstance(edits, list) or len(edits) != 60:
        raise ValueError('Unexpected migration manifest.')
    changes = []
    seen = set()
    for record in edits:
        name = record['path']
        if name in seen:
            raise ValueError(f'Duplicate path: {name}')
        seen.add(name)
        path = checked_path(name)
        if name in EXCLUDED:
            continue
        before = record['before']
        if before is None:
            if path.exists():
                raise ValueError(f'New migration target already exists: {name}')
            updated = record['content'].encode('utf-8')
        else:
            original = path.read_bytes()
            if digest(original) != before:
                raise ValueError(f'Concurrent or unexpected source change: {name}')
            lines = original.decode('utf-8').splitlines(keepends=True)
            previous_start = len(lines) + 1
            for start, end, replacement in reversed(record['edits']):
                if not (0 <= start <= end <= len(lines)) or end > previous_start:
                    raise ValueError(f'Invalid or overlapping edit in {name}')
                lines[start:end] = [replacement]
                previous_start = start
            updated = ''.join(lines).encode('utf-8')
        if digest(updated) != record['after']:
            raise ValueError(f'Candidate output digest mismatch: {name}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(updated)
        existing_mode = git('ls-files', '-s', '--', name).split()
        mode = existing_mode[0] if existing_mode else '100644'
        changes.append({'path': name, 'after': record['after'], 'mode': mode})
    for name in TRANSPORT_PATHS:
        Path(name).unlink()
    MANIFEST.write_text(json.dumps({'source_sha': source_sha, 'changes': changes}))
    print(f'Applied {len(changes)} digest-verified source files; temporary transport removed.')
    print('Browser tests: NOT RUN; administrator policy blocked local navigation.')
    print(git('diff', '--stat'))


def publish_tree() -> None:
    manifest = json.loads(MANIFEST.read_text())
    source = manifest['source_sha']
    if source != git('rev-parse', 'HEAD') or source != os.environ.get('GH_SOURCE_SHA'):
        raise ValueError('Source commit identity changed during verification.')
    if os.environ.get('GH_REPOSITORY') != REPOSITORY:
        raise ValueError('Unexpected publication repository.')
    base = api(f'git/commits/{source}')['tree']['sha']
    entries = []
    for item in manifest['changes']:
        path = checked_path(item['path'])
        raw = path.read_bytes()
        if digest(raw) != item['after']:
            raise ValueError(f'Tests unexpectedly modified source: {item["path"]}')
        entries.append({'path': item['path'], 'mode': item['mode'], 'type': 'blob',
                        'content': raw.decode('utf-8')})
    for name in TRANSPORT_PATHS:
        if Path(name).exists():
            raise ValueError('Temporary transport must not be published.')
        entries.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': None})
    tree = api('git/trees', {'base_tree': base, 'tree': entries})
    print('TESTED_CANDIDATE_TREE=' + tree['sha'])
    print('VERIFIED_SOURCE_COMMIT=' + source)
    print('VERIFIED_SOURCE_FILES=' + str(len(manifest['changes'])))
    print('No commits, branch ref updates, or merges performed by this workflow.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['apply', 'publish-tree'])
    # The temporary workflow uses conventional long switches.
    import sys
    arguments = [arg.removeprefix('--') for arg in sys.argv[1:]]
    args = parser.parse_args(arguments)
    if args.operation == 'apply':
        apply()
    else:
        publish_tree()
