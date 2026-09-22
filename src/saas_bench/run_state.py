"""Small, private run manifests and atomic JSON files."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import shutil


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


def copy_workspace(source, destination):
    source = Path(source).resolve()
    for path in source.rglob('*'):
        if path.is_symlink() and (Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(source)):
            raise ValueError('Workspace symlink escapes a portable checkpoint: ' + str(path.relative_to(source)))
    shutil.copytree(source, destination, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pid', '.server.port'))


def checkpoint_directory(run, checkpoint):
    if checkpoint.get('version') != 2:
        raise ValueError('Incomplete legacy checkpoint; version 2 required')
    snapshot_id = checkpoint['snapshot_id']
    if len(snapshot_id) != 32 or any(c not in '0123456789abcdef' for c in snapshot_id):
        raise ValueError('Invalid snapshot identifier')
    directory = Path(run) / 'checkpoints' / snapshot_id
    required = {'world.nmdb', 'session.json', 'server_state.json'}
    if not required.issubset(checkpoint['files']):
        raise ValueError('Incomplete checkpoint file list')
    for name, checksum in checkpoint['files'].items():
        if name not in required or file_hash(directory / name) != checksum:
            raise ValueError('Checkpoint checksum mismatch: ' + name)
    if tree_hash(directory / 'agent_workspace') != checkpoint['workspace_sha256']:
        raise ValueError('Checkpoint workspace checksum mismatch')
    return directory
