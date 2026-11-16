"""SQLite store for paused (waiting_for_user) agent runs.

Policies enforced here, independent of any model:
* a session expires after ``ttl_seconds`` (default 5 minutes) and its stored state is deleted;
* the user's answer is accepted once: ``claim`` atomically moves ``waiting`` to ``answered``, so a second
  or simultaneous answer is rejected;
* the stored state holds the question, retrieved evidence and counters, never the user's answer, and the row
  is deleted when the run finishes.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing

from .clarify import DEFAULT_TTL_SECONDS


class SessionError(Exception):
    """Base class; ``message`` is safe to show to the user."""
    message = '이어갈 수 없는 요청입니다.'


class SessionNotFound(SessionError):
    message = '이어갈 대화를 찾을 수 없습니다. 처음부터 다시 문의해 주세요.'


class SessionExpired(SessionError):
    message = '답변 가능 시간이 지났습니다. 처음부터 다시 문의해 주세요.'


class AlreadyAnswered(SessionError):
    message = '이 질문에는 이미 답변하셨습니다. 답변은 한 번만 받을 수 있습니다.'


class SessionExists(SessionError):
    message = '같은 대화가 이미 진행 중입니다.'


class SessionStore:
    def __init__(self, path, *, ttl_seconds=DEFAULT_TTL_SECONDS, clock=time.time):
        if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise ValueError('ttl_seconds must be positive')
        self.path, self.ttl_seconds, self.clock = str(path), ttl_seconds, clock
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions ('
                       'session_id TEXT PRIMARY KEY, state TEXT NOT NULL, question TEXT NOT NULL, '
                       "status TEXT NOT NULL CHECK (status IN ('waiting', 'answered')), "
                       'created_at REAL NOT NULL, expires_at REAL NOT NULL)')

    def _connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def purge_expired(self):
        """Delete expired sessions (and the evidence stored with them); returns how many."""
        with closing(self._connect()) as db:
            return db.execute('DELETE FROM sessions WHERE expires_at < ?', (self.clock(),)).rowcount

    def put_waiting(self, session_id, state, question):
        """Store a paused run. Returns the expiry time (epoch seconds)."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('session_id must be a nonempty string')
        now = self.clock()
        expires = now + self.ttl_seconds
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('DELETE FROM sessions WHERE expires_at < ?', (now,))
                if db.execute('SELECT 1 FROM sessions WHERE session_id = ?', (session_id,)).fetchone():
                    raise SessionExists()
                db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)',
                           (session_id, json.dumps(state, ensure_ascii=False), question, 'waiting', now, expires))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        return expires

    def claim(self, session_id):
        """Atomically accept the single answer; returns the stored state."""
        now = self.clock()
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT state, status, expires_at FROM sessions WHERE session_id = ?',
                                 (session_id,)).fetchone()
                if row is None:
                    raise SessionNotFound()
                if row[2] < now:
                    db.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
                    db.execute('COMMIT')
                    raise SessionExpired()
                if row[1] != 'waiting':
                    raise AlreadyAnswered()
                db.execute("UPDATE sessions SET status = 'answered' WHERE session_id = ?", (session_id,))
                db.execute('COMMIT')
            except (SessionNotFound, AlreadyAnswered):
                db.execute('ROLLBACK')
                raise
            except SessionExpired:
                raise
            except BaseException:
                db.execute('ROLLBACK')
                raise
        return json.loads(row[0])

    def release(self, session_id):
        """Give the answer back after an unexpected failure, so the user is not locked out."""
        with closing(self._connect()) as db:
            db.execute("UPDATE sessions SET status = 'waiting' WHERE session_id = ? AND status = 'answered'",
                       (session_id,))

    def finish(self, session_id):
        with closing(self._connect()) as db:
            db.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))

    def pending_question(self, session_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT question, expires_at FROM sessions WHERE session_id = ? AND status = ?',
                             (session_id, 'waiting')).fetchone()
        return None if row is None or row[1] < self.clock() else row[0]
