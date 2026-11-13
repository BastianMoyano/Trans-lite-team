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
            ''')
        self.recover()
        self._purge_quarantine()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def create_meeting(self, title='Meeting'):
        base = meeting_folder_name(title, local_now())
        with self._connect() as db:
            # Windows folder names are case-insensitive, so compare casefolded.
            taken = {row['id'].casefold() for row in db.execute('SELECT id FROM meetings')}
            taken.update(entry.name.casefold() for entry in self.root.iterdir())
            meeting_id, copy = base, 2
            while meeting_id.casefold() in taken:
                meeting_id, copy = f'{base} ({copy})', copy + 1
            db.execute('INSERT INTO meetings VALUES (?,?,?,?,?)',
                       (meeting_id, title, utc_now(), None, 'recording'))
        (self.root / meeting_id).mkdir(exist_ok=True)
        return meeting_id

    def finish_meeting(self, meeting_id, status='completed'):
        with self._connect() as db:
            db.execute('UPDATE meetings SET status=?, ended_utc=? WHERE id=?',
                       (status, utc_now(), meeting_id))

    def list_meetings(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM meetings ORDER BY started_utc DESC')]

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
        for leftover in self.root.glob('.delete-*'):
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

