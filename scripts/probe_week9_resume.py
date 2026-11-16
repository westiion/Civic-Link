"""Check a case-specific, persisted clarification with archived evidence."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import ROOT
from civic_link.agent.experiment import atomic_json, read_json, result_path


CASE_ID = 'W7V2-BH002'
WORK = ROOT / 'outputs/week8-608-analysis'
ANSWERS = {
    'internet': (
        '저장된 규정의 인터넷 신청 범위에는 자녀가 부모 기준으로 신청하는 경우가 포함됩니다. '
        '본인 확인과 별도 교부제한을 확인해야 합니다.',
        ('DOC-c95ed190ec5caede9cf4-C0017', 'DOC-74fb8e3f4207ccfdb0ce-C0028'),
    ),
    'kiosk': (
        '저장된 규정상 무인증명서발급기는 본인의 증명서로 한정됩니다.',
        ('DOC-c95ed190ec5caede9cf4-C0018', 'DOC-74fb8e3f4207ccfdb0ce-C0027'),
    ),
}
CLARIFICATION = '지금 보고 계신 화면이 인터넷 신청 사이트인가요, 아니면 무인발급기 화면인가요?'


def stored_context():
    run = WORK / 'content-sample60-run'
    row = read_json(result_path(run, 'iterative_search', CASE_ID))
    last_input = next(e for e in reversed(row['result']['events']) if e['stage'] == 'qwen_input')
    state = json.loads(last_input['messages'][1]['content'])['현재 실행 상태']
    return dict(query_id=CASE_ID, question=row['question'], evidence=state['evidence'],
                checks=state['checks'], history=state['history'], pending_field='channel')


def checkpoint(db, session_id, context, channel=None):
    if channel is not None and channel not in ANSWERS:
        raise ValueError('unsupported channel')
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS sessions ('
                           'id TEXT PRIMARY KEY, context TEXT NOT NULL, channel TEXT)')
        connection.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                           (session_id, json.dumps(context, ensure_ascii=False), channel))
    return CLARIFICATION if channel is None else channel_answer(context, channel)


def channel_answer(context, channel):
    text, refs = ANSWERS[channel]
    if not set(refs) <= {item['id'] for item in context['evidence']}:
        raise ValueError('saved evidence does not support the channel')
    return dict(channel=channel, answer=text, evidence_ids=list(refs), new_tool_calls=0)


def resume(db, session_id, channel):
    if channel not in ANSWERS:
        raise ValueError('unsupported channel')
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT context, channel FROM sessions WHERE id = ?',
                                 (session_id,)).fetchone()
        if row is None:
            raise ValueError('unknown session')
        context, previous_channel = json.loads(row[0]), row[1]
        if previous_channel is not None and previous_channel != channel:
            raise ValueError('conflicting reply to a completed session')
        result = channel_answer(context, channel)
        connection.execute('UPDATE sessions SET channel = ? WHERE id = ? AND channel IS NULL',
                           (channel, session_id))
    return dict(result, restored_context=context)


def main():
    context = stored_context()
    directory = WORK / 'feedback'
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as scratch:
        db = Path(scratch) / 'sessions.sqlite'
        asked = checkpoint(db, 'waiting-internet', context)
        checkpoint(db, 'waiting-kiosk', context)
        started = checkpoint(db, 'known-channel', context, channel='internet')
        internet = resume(db, 'waiting-internet', 'internet')
        repeated = resume(db, 'waiting-internet', 'internet')
        kiosk = resume(db, 'waiting-kiosk', 'kiosk')
        if internet.pop('restored_context') != context or kiosk.pop('restored_context') != context:
            raise AssertionError('persistence changed the saved context')
        repeated.pop('restored_context')
        if started != internet or repeated != internet or kiosk['channel'] != 'kiosk':
            raise AssertionError('known channel, retries, or another session changed the routing result')
        try:
            resume(db, 'waiting-internet', 'kiosk')
        except ValueError as exc:
            if str(exc) != 'conflicting reply to a completed session':
                raise
        else:
            raise AssertionError('a conflicting reply replaced a completed result')
    report = directory / 'resume-probe.json'
    atomic_json(report, dict(query_id=CASE_ID, clarification=asked, known_channel=started,
                             resumed_internet=internet, resumed_kiosk=kiosk,
                             restored_fields=list(context), saved_evidence_count=len(context['evidence']),
                             retry_idempotent=True, sessions_isolated=True, conflicting_reply_rejected=True))
    print(f'Results: {report}')


if __name__ == '__main__':
    main()
