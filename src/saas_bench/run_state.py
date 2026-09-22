"""Small, private run manifests and atomic JSON files."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(path):
    path = Path(path)
    files = {str(p.relative_to(path)): file_hash(p) for p in sorted(path.rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def artifact_hashes(public):
    public = Path(public)
    return {'bundle': file_hash(public / 'novamind-operation'),
            'sdk': tree_hash(public / 'docs' / 'novamind_api'),
            'docs': tree_hash(public / 'docs')}


def build_manifest(root, public):
    root = Path(root)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    patch = subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src', 'scripts/build_public.py'], cwd=root)
    return {'version': 1, 'source_commit': commit,
            'patch_sha256': hashlib.sha256(patch).hexdigest(),
            'source_sha256': tree_hash(root / 'src'),
            'python': platform.python_version(), 'artifacts': artifact_hashes(public)}


def verify_build(public):
    manifest = json.loads((Path(public) / 'build.json').read_text())
    if manifest['artifacts'] != artifact_hashes(public):
        raise ValueError('Public artifacts differ from build.json; rebuild the public bundle')
    if manifest['python'].split('.')[:2] != platform.python_version().split('.')[:2]:
        raise ValueError('Public bundle Python version does not match this interpreter')
    return manifest
