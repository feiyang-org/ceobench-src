"""Private SQL evidence, never mounted into the agent workspace.

Blob/version storage adapted from provenancefs/store.py at
924e7f7c3c094f8db18dfb17e2fe64c77ec1b843. Unlike record_write there,
every execution has its own version, including identical responses.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

FORMAT = 'ceobench.evidence-records.v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def whole_rows(body, losses=()):
    """Compare public rows only, with explicit types and duplicate multiplicity."""
    data = json.loads(body)
    if not data.get('success'):
        return {'status': 'unsupported', 'reason': 'query_failed'}, None
    columns = data['columns']
    reason = next(iter(losses), None)
    if len(set(columns)) != len(columns):
        reason = 'duplicate_columns'
    if reason:
        return {'status': 'unsupported', 'reason': reason}, None

    def typed(value):
        if value is None:
            return ['null']
        if isinstance(value, bool):
            return ['bool', value]
        if isinstance(value, int):
            return ['int', str(value)]
        if isinstance(value, float) and math.isfinite(value):
            return ['float64', value.hex()]
        if isinstance(value, str):
            return ['text', value]
        raise ValueError('unsupported_value')

    try:
        rows = [encoded([[col, typed(row[col])] for col in columns]).decode()
                for row in data['rows']]
    except (KeyError, ValueError) as exc:
        return {'status': 'unsupported', 'reason': str(exc)}, None
    content = encoded({'format': 'whole-row-v1', 'columns': columns, 'rows': sorted(rows)})
    return {'status': 'available', 'scope': 'returned_subset' if data.get('truncated') else 'complete_for_query'}, content


class SQLEvidenceStore:
    def __init__(self, path, identity):
        self.path = Path(path).resolve()
        self.identity = identity
        for key in ('run_id', 'branch_id', 'data_source_id'):
            if not re.fullmatch(r'[A-Za-z0-9_-]+', identity.get(key, '')):
                raise ValueError('Invalid evidence identity: ' + key)
        if identity.get('format') != FORMAT:
            raise ValueError('Unsupported evidence format')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fault_path = self.path.with_suffix('.fault.json')
        self.fault = None
        with closing(self.connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS identity (value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS branches (
                    id TEXT PRIMARY KEY, parent TEXT, fork_seq INTEGER);
                CREATE TABLE IF NOT EXISTS blobs (
                    content_hash TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, content BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS queries (id TEXT PRIMARY KEY, definition BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    event_id TEXT PRIMARY KEY, branch TEXT NOT NULL, seq INTEGER NOT NULL,
                    query_id TEXT, request BLOB NOT NULL, UNIQUE(branch, seq));
                CREATE TABLE IF NOT EXISTS results (
                    event_id TEXT PRIMARY KEY REFERENCES requests(event_id), record BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (
                    version_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES requests(event_id),
                    previous_version TEXT, content_hash TEXT NOT NULL REFERENCES blobs(content_hash),
                    metadata BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_id TEXT PRIMARY KEY REFERENCES requests(event_id), record BLOB NOT NULL);
            ''')
            source = encoded({k: identity[k] for k in ('run_id', 'data_source_id', 'format')})
            existing = conn.execute('SELECT value FROM identity').fetchone()
            if existing and existing[0] != source:
                raise ValueError('Evidence database identity mismatch')
            if not existing:
                conn.execute('INSERT INTO identity VALUES (?)', (source,))
            branch = identity['branch_id']
            parent, cutoff = identity.get('parent_branch'), identity.get('fork_seq')
            existing = conn.execute('SELECT parent, fork_seq FROM branches WHERE id=?', (branch,)).fetchone()
            if existing and tuple(existing) != (parent, cutoff):
                raise ValueError('Evidence branch ancestry mismatch')
            if not existing:
                if parent and (not conn.execute('SELECT 1 FROM branches WHERE id=?', (parent,)).fetchone()
                               or cutoff != self.sequence(conn, parent)):
                    raise ValueError('Fork must use the saved parent cutoff')
                conn.execute('INSERT INTO branches VALUES (?,?,?)', (branch, parent, cutoff))

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=1)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        return conn

    def sequence(self, conn, branch=None):
        return conn.execute('SELECT coalesce(max(seq),0) FROM requests WHERE branch=?',
                            (branch or self.identity['branch_id'],)).fetchone()[0]

    def fail(self, exc):
        from .run_state import write_json
        self.fault = {'reason': str(exc), 'time': now(), 'capture_status': 'missing'}
        try:
            write_json(self.fault_path, self.fault)
        except OSError:
            pass  # The server's in-memory fault also blocks checkpoint publication.

    def assert_healthy(self):
        if self.fault or self.fault_path.exists():
            raise RuntimeError('SQL evidence capture failed; collection stopped')
        with closing(self.connect()) as conn:
            pending = conn.execute('''SELECT event_id FROM requests
                WHERE event_id NOT IN (SELECT event_id FROM results)
                   OR event_id NOT IN (SELECT event_id FROM deliveries) LIMIT 1''').fetchone()
        if pending:
            raise RuntimeError('Unconfirmed SQL evidence outcome: ' + pending[0])

    def begin(self, raw_body, policy):
        if self.fault or self.fault_path.exists():
            raise RuntimeError('SQL evidence capture failed; collection stopped')
        try:
            body = json.loads(raw_body) if raw_body else {}
        except (ValueError, UnicodeDecodeError):
            body = None
        sql = body.get('sql') if isinstance(body, dict) else None
        definition = ["query-v1", self.identity['run_id'], self.identity['branch_id'],
                      self.identity['data_source_id'], policy, sql, None]
        query = digest(encoded(definition)) if isinstance(sql, str) and sql.strip() else None
        with closing(self.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            seq = self.sequence(conn) + 1
            event = f"{self.identity['run_id']}/{self.identity['branch_id']}/{seq}"
            if query:
                conn.execute('INSERT OR IGNORE INTO queries VALUES (?,?)', (query, encoded(definition)))
            request = dict(event_id=event, seq=seq, author='ceo', kind='sql_query',
                           started_at=now(), candidate_sql=sql, bound_parameters=None,
                           raw_request_hex=raw_body.hex(), parent_event_id=None,
                           parent_reason='unpropagated_context', query_id=query)
            conn.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                         (event, self.identity['branch_id'], seq, query, encoded(request)))
        return event

    def finish(self, event, status, headers, body, execution):
        started = time.monotonic()
        comparison, content = whole_rows(body, execution.get('losses', ()))
        result = json.loads(body)
        state = ('succeeded' if status == 200 else 'timed_out' if status == 504 else
                 'rejected' if status in (400, 403) else 'failed')
        record = dict(execution, status=state, http_status=status, headers=headers,
                      completed_at=now(), capture_status='complete', capture_gaps=[],
                      result_coverage=('truncated' if result.get('truncated') else
                                       'complete_for_query') if status == 200 else 'unknown',
                      comparison=comparison, receive_state='unknown')
        with closing(self.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            request = conn.execute('SELECT query_id FROM requests WHERE event_id=?', (event,)).fetchone()
            previous = conn.execute('''SELECT v.version_id FROM versions v JOIN requests r USING(event_id)
                WHERE r.query_id=? AND v.version_id LIKE '%:public_response'
                ORDER BY v.rowid DESC LIMIT 1''', (request[0],)).fetchone()
            for slot, payload in [('public_response', body), ('comparison', content)]:
                if payload is None:
                    continue
                sha = digest(payload)
                conn.execute('INSERT OR IGNORE INTO blobs VALUES (?,?,?)', (sha, len(payload), payload))
                metadata = dict(layer='server_public_response' if slot == 'public_response' else 'comparison',
                                object_id=request[0], created_by_event=event,
                                extent='full', source_truncated=bool(result.get('truncated')),
                                acquired_at=record['completed_at'],
                                content_time={'status': 'unknown', 'reason': 'not_declared'})
                if slot == 'comparison':
                    metadata['derived_from'] = event + ':public_response'
                conn.execute('INSERT INTO versions VALUES (?,?,?,?,?)',
                             (event + ':' + slot, event,
                              previous[0] if previous and slot == 'public_response' else None,
                              sha, encoded(metadata)))
            record['capture_seconds'] = time.monotonic() - started
            conn.execute('INSERT INTO results VALUES (?,?)', (event, encoded(record)))

    def delivered(self, event, state):
        with closing(self.connect()) as conn, conn:
            conn.execute('INSERT INTO deliveries VALUES (?,?)',
                         (event, encoded(dict(send_state=state, receive_state='unknown', time=now()))))

    def _visible(self, conn, event):
        row = conn.execute('SELECT branch,seq FROM requests WHERE event_id=?', (event,)).fetchone()
        if row:
            branch, limit = self.identity['branch_id'], None
            while branch:
                if row['branch'] == branch and (limit is None or row['seq'] <= limit):
                    return
                ancestry = conn.execute('SELECT parent,fork_seq FROM branches WHERE id=?', (branch,)).fetchone()
                branch, limit = ancestry
        raise KeyError('Evidence is outside this branch: ' + event)

    def read_event(self, event):
        with closing(self.connect()) as conn:
            self._visible(conn, event)
            request = json.loads(conn.execute('SELECT request FROM requests WHERE event_id=?', (event,)).fetchone()[0])
            result = conn.execute('SELECT record FROM results WHERE event_id=?', (event,)).fetchone()
            delivery = conn.execute('SELECT record FROM deliveries WHERE event_id=?', (event,)).fetchone()
            definition = conn.execute('SELECT definition FROM queries WHERE id=?', (request['query_id'],)).fetchone()
            return dict(request=request, result=json.loads(result[0]) if result else
                        dict(status='result_unknown', completed_at=None, capture_status='missing'),
                        query_definition=json.loads(definition[0]) if definition else None,
                        delivery=json.loads(delivery[0]) if delivery else
                        dict(send_state='unknown', receive_state='unknown'))

    def get_content(self, version):
        with closing(self.connect()) as conn:
            row = conn.execute('SELECT * FROM versions WHERE version_id=?', (version,)).fetchone()
            if row is None:
                raise KeyError(version)
            self._visible(conn, row['event_id'])
            blob = conn.execute('SELECT content,size_bytes FROM blobs WHERE content_hash=?', (row['content_hash'],)).fetchone()
            if blob is None or len(blob[0]) != blob[1] or digest(blob[0]) != row['content_hash']:
                raise ValueError('Evidence blob checksum mismatch')
            return dict(version_id=version, previous_version=row['previous_version'],
                        blob_sha256=row['content_hash'], **json.loads(row['metadata'])), bytes(blob[0])

    def snapshot(self, target):
        self.assert_healthy()
        with closing(self.connect()) as conn, closing(sqlite3.connect(target)) as backup:
            conn.backup(backup)
            return dict(identity=self.identity, cutoff=self.sequence(conn), sha256=digest(Path(target).read_bytes()))
