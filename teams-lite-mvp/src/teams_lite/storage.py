"""SQLite metadata and crash-recoverable audio segment persistence."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
import uuid
import wave
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def local_now():
    return datetime.now()


# Keeps <data>/<meeting>/<segment file> well under the 260-character Windows path limit.
MAX_FOLDER_TITLE_CHARS = 40
_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def meeting_folder_name(title, moment):
    """Readable, sortable folder name: '2026-11-13 07-44 Title'."""
    safe = re.sub(r'\s+', ' ', _INVALID_NAME_CHARS.sub('_', str(title))).strip()
    safe = safe[:MAX_FOLDER_TITLE_CHARS].rstrip(' .')
    stamp = moment.strftime('%Y-%m-%d %H-%M')
    return f'{stamp} {safe}' if safe else stamp


def _clear_readonly_and_retry(function, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _purge_directory(path, attempts=5, delay=0.2):
    """Best-effort removal: OneDrive, antivirus or the indexer may briefly lock files."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, onexc=_clear_readonly_and_retry)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(delay * (attempt + 1))
    return False


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


_IMPORTABLE_STATUSES = {'completed', 'interrupted', 'failed'}

ANY = object()
"""Filter value for Storage.list_meetings meaning 'do not filter on this column'."""


def _clean_name(name):
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    if not name:
        raise ValueError('Escribe un nombre')
    return name


def _check_unique(db, query, params, message):
    if db.execute(query, params).fetchone() is not None:
        raise ValueError(message)


def _check_placement(db, organization_id, project_id):
    """A meeting sits in the vault (no organization), in an organization, or in one of its projects."""
    if organization_id is not None and db.execute(
            'SELECT 1 FROM organizations WHERE id=?', (organization_id,)).fetchone() is None:
        raise ValueError('Organización desconocida')
    if project_id is not None and db.execute(
            'SELECT 1 FROM projects WHERE id=? AND organization_id IS ?',
            (project_id, organization_id)).fetchone() is None:
        raise ValueError('El proyecto no pertenece a la organización elegida')


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / 'meetings.sqlite3'
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, started_utc TEXT NOT NULL,
                    ended_utc TEXT, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS streams (
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    kind TEXT NOT NULL, sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY, stream_id TEXT NOT NULL REFERENCES streams(id),
                    sequence INTEGER NOT NULL, started_monotonic REAL NOT NULL,
                    started_utc TEXT NOT NULL, ended_utc TEXT, path TEXT NOT NULL,
                    temp_path TEXT NOT NULL, frames INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL, UNIQUE(stream_id, sequence));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                    timestamp_utc TEXT NOT NULL, monotonic REAL NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    created_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
                    name TEXT NOT NULL COLLATE NOCASE, created_utc TEXT NOT NULL,
                    UNIQUE(organization_id, name));
            ''')
            # Additive migration: older databases get the placement columns, rows stay untouched.
            # A meeting without an organization lives in the "Cofre de reuniones".
            columns = {row['name'] for row in db.execute('PRAGMA table_info(meetings)')}
            if 'organization_id' not in columns:
                db.execute('ALTER TABLE meetings ADD COLUMN organization_id TEXT REFERENCES organizations(id)')
            if 'project_id' not in columns:
                db.execute('ALTER TABLE meetings ADD COLUMN project_id TEXT REFERENCES projects(id)')
        self.recover()
        self._purge_quarantine()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def _unique_meeting_id(self, db, base):
        # Windows folder names are case-insensitive, so compare casefolded.
        taken = {row['id'].casefold() for row in db.execute('SELECT id FROM meetings')}
        taken.update(entry.name.casefold() for entry in self.root.iterdir())
        meeting_id, copy = base, 2
        while meeting_id.casefold() in taken:
            meeting_id, copy = f'{base} ({copy})', copy + 1
        return meeting_id

    def get_meeting(self, meeting_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM meetings WHERE id=?', (meeting_id,)).fetchone()
            return dict(row) if row is not None else None

    def create_meeting(self, title='Meeting', organization_id=None, project_id=None):
        base = meeting_folder_name(title, local_now())
        with self._connect() as db:
            _check_placement(db, organization_id, project_id)
            meeting_id = self._unique_meeting_id(db, base)
            db.execute(
                'INSERT INTO meetings (id, title, started_utc, ended_utc, status, organization_id, project_id) '
                'VALUES (?,?,?,?,?,?,?)',
                (meeting_id, title, utc_now(), None, 'recording', organization_id, project_id))
        (self.root / meeting_id).mkdir(exist_ok=True)
        return meeting_id

    def finish_meeting(self, meeting_id, status='completed'):
        with self._connect() as db:
            db.execute('UPDATE meetings SET status=?, ended_utc=? WHERE id=?',
                       (status, utc_now(), meeting_id))

    def list_meetings(self, *, organization_id=ANY, project_id=ANY):
        """Newest first. A filter left as ANY is ignored; None matches the vault or 'no project'."""
        clauses, params = [], []
        for column, value in (('organization_id', organization_id), ('project_id', project_id)):
            if value is ANY:
                continue
            if value is None:
                clauses.append(f'{column} IS NULL')
            else:
                clauses.append(f'{column}=?')
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                f'SELECT * FROM meetings{where} ORDER BY started_utc DESC', params)]

    def adopt_meeting(self, staging, meeting, *, organization_id=None, project_id=None,
                      streams=(), segments=(), events=()):
        """Records an imported meeting whose files were extracted to `staging`, all or nothing.

        `staging` must be a folder directly under the data root; it becomes the meeting folder.
        Streams and segments get fresh ids, so importing the same package twice never collides.
        Each segment names its extracted WAV with `file`, relative to `staging`.
        """
        staging = Path(staging).resolve()
        if staging.parent != self.root or not staging.is_dir():
            raise ValueError('Carpeta de importación no válida')
        started = datetime.fromisoformat(meeting['started_utc'])
        moment = started.astimezone() if started.tzinfo else started
        status = meeting['status'] if meeting['status'] in _IMPORTABLE_STATUSES else 'completed'
        db = self._connect()
        moved = False
        try:
            db.execute('BEGIN IMMEDIATE')
            _check_placement(db, organization_id, project_id)
            meeting_id = self._unique_meeting_id(db, meeting_folder_name(meeting['title'], moment))
            final_dir = self.root / meeting_id
            db.execute(
                'INSERT INTO meetings (id, title, started_utc, ended_utc, status, organization_id, project_id) '
                'VALUES (?,?,?,?,?,?,?)',
                (meeting_id, meeting['title'], meeting['started_utc'], meeting['ended_utc'], status,
                 organization_id, project_id))
            stream_ids = {}
            for stream in streams:
                stream_ids[stream['id']] = (uuid.uuid4().hex, stream['kind'])
                db.execute('INSERT INTO streams VALUES (?,?,?,?,?)',
                           (stream_ids[stream['id']][0], meeting_id, stream['kind'],
                            stream['sample_rate'], stream['channels']))
            for segment in segments:
                stream_id, kind = stream_ids[segment['stream_id']]
                name = f"{kind}-{stream_id}-{segment['sequence']:06d}.wav"
                os.replace(staging / segment['file'], staging / name)
                final = final_dir / name
                db.execute(
                    'INSERT INTO segments (id, stream_id, sequence, started_monotonic, started_utc, ended_utc, '
                    'path, temp_path, frames, status) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, stream_id, segment['sequence'], segment['started_monotonic'],
                     segment['started_utc'], segment['ended_utc'], str(final), str(final.with_suffix('.pcm.part')),
                     segment['frames'], segment['status']))
            for event in events:
                db.execute('INSERT INTO events (meeting_id,timestamp_utc,monotonic,kind,detail) VALUES (?,?,?,?,?)',
                           (meeting_id, event['timestamp_utc'], event['monotonic'], event['kind'], event['detail']))
            db.execute('INSERT INTO events (meeting_id,timestamp_utc,monotonic,kind,detail) VALUES (?,?,?,?,?)',
                       (meeting_id, utc_now(), time.monotonic(), 'imported', 'Importada desde un paquete'))
            os.replace(staging, final_dir)
            moved = True
            db.commit()
        except Exception:
            db.rollback()
            if moved and final_dir.exists() and not staging.exists():
                os.replace(final_dir, staging)
            raise
        finally:
            db.close()
        return meeting_id

    def move_meeting(self, meeting_id, organization_id, project_id):
        """Files a meeting elsewhere; only metadata changes, its folder stays where it is."""
        with self._connect() as db:
            _check_placement(db, organization_id, project_id)
            moved = db.execute('UPDATE meetings SET organization_id=?, project_id=? WHERE id=?',
                               (organization_id, project_id, meeting_id))
            if moved.rowcount == 0:
                raise ValueError('Reunión desconocida')

    def create_organization(self, name):
        name = _clean_name(name)
        organization_id = uuid.uuid4().hex
        with self._connect() as db:
            _check_unique(db, 'SELECT 1 FROM organizations WHERE name=?', (name,),
                          f'Ya existe una organización llamada "{name}"')
            db.execute('INSERT INTO organizations (id, name, created_utc) VALUES (?,?,?)',
                       (organization_id, name, utc_now()))
        return organization_id

    def rename_organization(self, organization_id, name):
        name = _clean_name(name)
        with self._connect() as db:
            _check_unique(db, 'SELECT 1 FROM organizations WHERE name=? AND id<>?', (name, organization_id),
                          f'Ya existe una organización llamada "{name}"')
            if db.execute('UPDATE organizations SET name=? WHERE id=?', (name, organization_id)).rowcount == 0:
                raise ValueError('Organización desconocida')

    def delete_organization(self, organization_id):
        """Deletes the organization and its projects; its meetings go back to the vault."""
        with self._connect() as db:
            db.execute('UPDATE meetings SET organization_id=NULL, project_id=NULL WHERE organization_id=?',
                       (organization_id,))
            db.execute('DELETE FROM projects WHERE organization_id=?', (organization_id,))
            db.execute('DELETE FROM organizations WHERE id=?', (organization_id,))

    def list_organizations(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute('''
                SELECT organizations.*,
                       (SELECT COUNT(*) FROM meetings WHERE meetings.organization_id=organizations.id)
                           AS meeting_count
                FROM organizations ORDER BY name COLLATE NOCASE''')]

    def create_project(self, organization_id, name):
        name = _clean_name(name)
        project_id = uuid.uuid4().hex
        with self._connect() as db:
            if db.execute('SELECT 1 FROM organizations WHERE id=?', (organization_id,)).fetchone() is None:
                raise ValueError('Organización desconocida')
            _check_unique(db, 'SELECT 1 FROM projects WHERE organization_id=? AND name=?', (organization_id, name),
                          f'Ya existe un proyecto llamado "{name}" en esta organización')
            db.execute('INSERT INTO projects (id, organization_id, name, created_utc) VALUES (?,?,?,?)',
                       (project_id, organization_id, name, utc_now()))
        return project_id

    def rename_project(self, project_id, name):
        name = _clean_name(name)
        with self._connect() as db:
            project = db.execute('SELECT organization_id FROM projects WHERE id=?', (project_id,)).fetchone()
            if project is None:
                raise ValueError('Proyecto desconocido')
            _check_unique(db, 'SELECT 1 FROM projects WHERE organization_id=? AND name=? AND id<>?',
                          (project['organization_id'], name, project_id),
                          f'Ya existe un proyecto llamado "{name}" en esta organización')
            db.execute('UPDATE projects SET name=? WHERE id=?', (name, project_id))

    def delete_project(self, project_id):
        """Deletes the project; its meetings stay in the organization without a project."""
        with self._connect() as db:
            db.execute('UPDATE meetings SET project_id=NULL WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM projects WHERE id=?', (project_id,))

    def list_projects(self, organization_id):
        with self._connect() as db:
            return [dict(row) for row in db.execute('''
                SELECT projects.*,
                       (SELECT COUNT(*) FROM meetings WHERE meetings.project_id=projects.id) AS meeting_count
                FROM projects WHERE organization_id=? ORDER BY name COLLATE NOCASE''', (organization_id,))]

    def delete_meeting(self, meeting_id):
        meeting_dir = (self.root / meeting_id).resolve()
        if meeting_dir.parent != self.root:
            raise ValueError('ID de reunión no válido')
        # Not named after the meeting: a long title would push nested paths past 260 chars.
        quarantine = self.root / f'.delete-{uuid.uuid4().hex}'
        moved = False
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            meeting = db.execute('SELECT status FROM meetings WHERE id=?', (meeting_id,)).fetchone()
            if meeting is None:
                raise ValueError('Reunión desconocida')
            if meeting['status'] == 'recording':
                raise RuntimeError('No se puede eliminar una reunión mientras se está grabando')
            if meeting_dir.exists():
                os.replace(meeting_dir, quarantine)
                moved = True
            db.execute(
                'DELETE FROM segments WHERE stream_id IN '
                '(SELECT id FROM streams WHERE meeting_id=?)',
                (meeting_id,),
            )
            db.execute('DELETE FROM streams WHERE meeting_id=?', (meeting_id,))
            db.execute('DELETE FROM events WHERE meeting_id=?', (meeting_id,))
            db.execute('DELETE FROM meetings WHERE id=?', (meeting_id,))
            db.commit()
        except Exception:
            db.rollback()
            if moved and quarantine.exists() and not meeting_dir.exists():
                os.replace(quarantine, meeting_dir)
            raise
        finally:
            db.close()
        if moved:
            # The meeting is already deleted: a folder Windows keeps locked is retried
            # here and swept again on the next startup, never reported as a failure.
            _purge_directory(quarantine)

    def _purge_quarantine(self):
        # .delete-* holds deleted meetings, .import-* an import interrupted before it was recorded.
        for pattern in ('.delete-*', '.import-*'):
            for leftover in self.root.glob(pattern):
                if leftover.is_dir():
                    _purge_directory(leftover, attempts=1)

    def create_stream(self, meeting_id, kind, sample_rate, channels):
        stream_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('INSERT INTO streams VALUES (?,?,?,?,?)',
                       (stream_id, meeting_id, kind, sample_rate, channels))
        return stream_id

    def begin_segment(self, stream_id, sequence, started_monotonic, started_utc):
        segment_id = uuid.uuid4().hex
        with self._connect() as db:
            stream = db.execute('SELECT * FROM streams WHERE id=?', (stream_id,)).fetchone()
            if stream is None:
                raise ValueError('Flujo de audio desconocido')
            directory = self.root / stream['meeting_id']
            directory.mkdir(exist_ok=True)
            final = directory / f"{stream['kind']}-{stream_id}-{sequence:06d}.wav"
            temp = final.with_suffix('.pcm.part')
            db.execute('INSERT INTO segments (id, stream_id, sequence, started_monotonic, started_utc, path, temp_path, status) VALUES (?,?,?,?,?,?,?,?)',
                       (segment_id, stream_id, sequence, started_monotonic, started_utc,
                        str(final), str(temp), 'writing'))
        return {'id': segment_id, 'path': str(final), 'temp_path': str(temp)}

    def finalize_segment(self, segment_id, recovered=False):
        with self._connect() as db:
            row = db.execute('''SELECT segments.*, streams.sample_rate, streams.channels
                                FROM segments JOIN streams ON streams.id=segments.stream_id
                                WHERE segments.id=?''', (segment_id,)).fetchone()
            if row is None:
                raise ValueError('Segmento de audio desconocido')
            if row['status'] != 'writing':
                return
            temp, final = Path(row['temp_path']), Path(row['path'])
            wav_temp = final.with_suffix('.wav.part')
            if temp.exists():
                frame_bytes = 2 * row['channels']
                size = temp.stat().st_size
                frames = size // frame_bytes
                if frames == 0:
                    temp.unlink()
                    db.execute('UPDATE segments SET status=?, ended_utc=? WHERE id=?', ('empty', utc_now(), segment_id))
                    return
                with temp.open('rb') as source, wave.open(str(wav_temp), 'wb') as wav:
                    wav.setnchannels(row['channels'])
                    wav.setsampwidth(2)
                    wav.setframerate(row['sample_rate'])
                    remaining = frames * frame_bytes
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise IOError('Segment data ended unexpectedly')
                        wav.writeframesraw(chunk)
                        remaining -= len(chunk)
                with wav_temp.open('rb+') as output:
                    os.fsync(output.fileno())
                os.replace(wav_temp, final)
            elif final.exists():
                with wave.open(str(final), 'rb') as wav:
                    frames = wav.getnframes()
            else:
                db.execute('UPDATE segments SET status=?, ended_utc=? WHERE id=?', ('missing', utc_now(), segment_id))
                return
            db.execute('UPDATE segments SET frames=?, status=?, ended_utc=? WHERE id=?',
                       (frames, 'recovered' if recovered else 'finalized', utc_now(), segment_id))
        # Remove the raw copy only after the database transaction commits.
        temp.unlink(missing_ok=True)

    def list_segments(self, meeting_id):
        with self._connect() as db:
            rows = db.execute('''SELECT segments.*, streams.kind, streams.sample_rate, streams.channels
                FROM segments JOIN streams ON streams.id=segments.stream_id
                WHERE streams.meeting_id=? ORDER BY segments.started_utc, segments.sequence''', (meeting_id,))
            return [dict(row) for row in rows]

    def add_event(self, meeting_id, kind, detail):
        with self._connect() as db:
            db.execute('INSERT INTO events (meeting_id,timestamp_utc,monotonic,kind,detail) VALUES (?,?,?,?,?)',
                       (meeting_id, utc_now(), time.monotonic(), kind, str(detail)))

    def list_events(self, meeting_id):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE meeting_id=? ORDER BY id', (meeting_id,))]

    def recover(self):
        with self._connect() as db:
            pending = [row['id'] for row in db.execute("SELECT id FROM segments WHERE status='writing'")]
        for segment_id in pending:
            self.finalize_segment(segment_id, recovered=True)
        with self._connect() as db:
            db.execute("UPDATE meetings SET status='interrupted', ended_utc=? WHERE status='recording'", (utc_now(),))

