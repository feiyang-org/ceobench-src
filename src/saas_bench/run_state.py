"""Small, private run manifests and atomic JSON files."""

import hashlib
from importlib.metadata import version
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
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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


def runtime_versions():
    packages = ('openai', 'anthropic', 'httpx', 'numpy', 'pydantic', 'boto3', 'botocore', 'cryptography',
                'sqlcipher3-binary' if platform.system() == 'Linux' else 'sqlcipher3')
    return {name: version(name) for name in packages}


def build_manifest(root, public):
    root = Path(root)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    patch = subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src', 'scripts/build_public.py'], cwd=root)
    return {'version': 1, 'source_commit': commit,
            'patch_sha256': hashlib.sha256(patch).hexdigest(),
            'source_sha256': tree_hash(root / 'src'),
            'builder_sha256': file_hash(root / 'scripts' / 'build_public.py'),
            'python': platform.python_version(), 'runtime': runtime_versions(),
            'artifacts': artifact_hashes(public)}


def verify_build(public, root=None):
    manifest = json.loads((Path(public) / 'build.json').read_text())
    if manifest['artifacts'] != artifact_hashes(public):
        raise ValueError('Public artifacts differ from build.json; rebuild the public bundle')
    if manifest['python'] != platform.python_version():
        raise ValueError('Public bundle Python version does not match this interpreter')
    if manifest.get('runtime') != runtime_versions():
        raise ValueError('Runtime SDK or dependency versions differ from build.json')
    if root is not None:
        root = Path(root)
        if manifest.get('source_sha256') != tree_hash(root / 'src') or manifest.get('builder_sha256') != file_hash(root / 'scripts' / 'build_public.py'):
            raise ValueError('Current source differs from the registered build; rebuild public artifacts')
    return manifest


def copy_workspace(source, destination, *, omit_session_world=None):
    source = Path(source).resolve()
    for path in source.rglob('*'):
        if path.is_symlink() and (Path(os.readlink(path)).is_absolute() or not path.resolve().is_relative_to(source)):
            raise ValueError('Workspace symlink escapes a portable checkpoint: ' + str(path.relative_to(source)))
    base_ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pid', '.server.port')
    def ignore(directory, names):
        ignored = base_ignore(directory, names)
        if omit_session_world is not None and Path(directory) == source / 'sessions' / omit_session_world:
            ignored.add('world.nmdb')
        return ignored
    shutil.copytree(source, destination, symlinks=True,
                    ignore=ignore)


def checkpoint_directory(run, checkpoint):
    if checkpoint.get('version') != 2:
        raise ValueError('Incomplete legacy checkpoint; version 2 required')
    snapshot_id = checkpoint['snapshot_id']
    if len(snapshot_id) != 32 or any(c not in '0123456789abcdef' for c in snapshot_id):
        raise ValueError('Invalid snapshot identifier')
    directory = Path(run) / 'checkpoints' / snapshot_id
    required = {'world.nmdb', 'session.json', 'server_state.json', 'manifest.json'}
    if not required.issubset(checkpoint['files']):
        raise ValueError('Incomplete checkpoint file list')
    for name, checksum in checkpoint['files'].items():
        if name not in required or file_hash(directory / name) != checksum:
            raise ValueError('Checkpoint checksum mismatch: ' + name)
    for name, checksum in checkpoint['request_logs'].items():
        if name not in ('agent_requests.jsonl', 'simulator_requests.jsonl') or file_hash(directory / 'request_logs' / name) != checksum:
            raise ValueError('Checkpoint request log checksum mismatch: ' + name)
    if tree_hash(directory / 'agent_workspace') != checkpoint['workspace_sha256']:
        raise ValueError('Checkpoint workspace checksum mismatch')
    evidence = checkpoint.get('sql_evidence')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if bool(evidence) != bool(manifest.get('sql_evidence')):
        raise ValueError('Checkpoint SQL evidence is missing or unexpected')
    if evidence and (evidence['identity'] != manifest['sql_evidence'] or
                     file_hash(directory / 'sql-evidence.sqlite') != evidence['sha256']):
        raise ValueError('Checkpoint SQL evidence checksum or identity mismatch')
    return directory


def restore_sql_evidence(run, directory, checkpoint, identity):
    from contextlib import closing
    from .sql_evidence import SQLEvidenceStore
    evidence = checkpoint.get('sql_evidence')
    if not evidence:
        raise ValueError('Capture cannot resume from a checkpoint without evidence')
    path = Path(run) / 'sql-evidence.sqlite'
    if path.exists():
        store = SQLEvidenceStore(path, identity)
        store.assert_healthy()
        with closing(store.connect()) as conn:
            # Never roll back a live ledger and reuse an already allocated sequence.
            if identity != evidence['identity'] or store.sequence(conn) != evidence['cutoff']:
                raise ValueError('SQL evidence has uncheckpointed events; preserve it for reconciliation')
    else:
        if identity != evidence['identity']:
            if (identity.get('parent_branch') != evidence['identity']['branch_id'] or
                    identity.get('fork_seq') != evidence['cutoff'] or
                    identity['run_id'] != evidence['identity']['run_id'] or
                    identity['data_source_id'] != evidence['identity']['data_source_id']):
                raise ValueError('Invalid SQL evidence fork identity')
        shutil.copy2(Path(directory) / 'sql-evidence.sqlite', path)
        store = SQLEvidenceStore(path, identity)
        store.assert_healthy()


def clone_sql_run(source, destination, branch_id):
    """Clone a frozen SQL-capture checkpoint, assigning an explicit new branch."""
    import re
    source, destination = Path(source), Path(destination)
    checkpoint = json.loads((source / 'checkpoint.json').read_text())
    directory = checkpoint_directory(source, checkpoint)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', branch_id):
        raise ValueError('Invalid evidence branch ID')
    manifest = json.loads((directory / 'manifest.json').read_text())
    parent = manifest['sql_evidence']
    from contextlib import closing
    import sqlite3
    with closing(sqlite3.connect(f'file:{directory / "sql-evidence.sqlite"}?mode=ro', uri=True)) as conn:
        if conn.execute('SELECT 1 FROM branches WHERE id=?', (branch_id,)).fetchone():
            raise ValueError('Clone branch ID already exists')
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / 'checkpoints' / checkpoint['snapshot_id']
    target.parent.mkdir()
    shutil.copytree(directory, target)
    shutil.copy2(source / 'config.json', destination / 'config.json')
    manifest['sql_evidence'] = dict(parent, branch_id=branch_id, parent_branch=parent['branch_id'],
                                    fork_seq=checkpoint['sql_evidence']['cutoff'],
                                    source_manifest_sha256=file_hash(directory / 'manifest.json'))
    write_json(destination / 'manifest.json', manifest)
    write_json(destination / 'checkpoint.json', checkpoint)
    return destination
