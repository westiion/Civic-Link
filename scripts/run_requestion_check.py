"""End-to-end check of the re-questioning policy with a real Qwen on all 60 clarify questions.

Each question is started once from the archived first search (gate + ask_user enabled). If it pauses, the
paused session is copied and answered in several ways, so every scenario starts from the same state:
  human_reply    the benchmark's human-written user answer (only the 5 questions that have one)
  unknown_reply  "모르겠어요"           -> expects status needs_information and no further model call
  pii_reply      an answer containing a fake resident registration number -> the number must appear nowhere
                 (model inputs, follow-up search query, stored session, events)
The follow-up search is an ORACLE (first-search evidence + gold chunks when the benchmark has them), so answer
quality is an upper bound; what is checked is the mechanics and the policies. Scoring is code only: whether
the cited evidence covers the resolved gold requirements (5 questions), and the policy checks above.
"""

import argparse
import copy
import json
import os
import shutil
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent import AgentConfig, InteractiveAgent, SessionStore
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import atomic_json, read_json, result_path
from civic_link.agent.index import load_corpus
from civic_link.agent.prompts import corpus_scope, describe_tools
from run_week9_probes import ARCHIVE, BENCHMARK, DB, WORK, initial_evidence, load_questions

OUT = WORK / 'feedback' / 'requestion-check'
FAKE_RRN = '901231-1234567'
KEYS = ('id', 'document_id', 'title', 'text', 'source')


class Oracle:
    def __init__(self, first, extra_ids, corpus):
        rows = {r['id']: r for r in first}
        rows.update({cid: corpus[cid] for cid in extra_ids})
        self.rows, self.calls = [{k: r[k] for k in KEYS} for r in rows.values()], []

    def run(self, arguments):
        self.calls.append(arguments)
        return {'tool': 'search', 'status': 'ok', 'evidence': copy.deepcopy(self.rows), 'relations': []}


class Unused:
    def run(self, arguments):
        raise RuntimeError('graph is disabled in this check')


class Recording:
    def __init__(self, client):
        self.client, self.sent = client, []

    @property
    def last_usage(self):
        return getattr(self.client, 'last_usage', None)

    def complete(self, messages):
        self.sent.append(messages)
        return self.client.complete(messages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--workers', type=int, default=1, help='questions run at the same time')
    args = parser.parse_args()
    questions = [q for q in load_questions(BENCHMARK / 'questions.jsonl') if q['expected_action'] == 'clarify']
    if args.dry_run:
        print(len(questions), 'clarify questions;',
              sum('simulated_user_response' in q['clarification'] for q in questions), 'with a human-written answer')
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    base = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                      timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                      json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    corpus = {r['id']: r for r in load_corpus(DB)}
    scope, descriptions, config = corpus_scope(DB), describe_tools(), AgentConfig(8, 6, 2, 60000)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def build(db, client, oracle):
        return InteractiveAgent(client, {'search': oracle, 'graph': Unused()}, scope=scope,
                                tool_descriptions=descriptions, config=config, allowed_tools=('search',),
                                store=SessionStore(db), use_gate=True, allow_ask=True)

    def run_question(q):
        qid, cl = q['question_id'], q['clarification']
        path = result_path(args.output_dir, 'question', qid)
        if path.is_file():
            return read_json(path)
        print(qid, flush=True)
        initial = read_json(result_path(ARCHIVE, 'initial', qid))['result']
        extra = [cid for req in cl.get('resolved_evidence_requirements', [])
                 for cid in req['acceptable_chunk_ids']]
        human = cl.get('simulated_user_response')
        replies = {'unknown_reply': '모르겠어요',
                   'pii_reply': (f'{human} ' if human else '') + f'제 주민번호는 {FAKE_RRN}예요'}
        if human:
            replies = {'human_reply': human, **replies}
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            agent = build(scratch / 'start.sqlite', Recording(base), Oracle(initial['evidence'], extra, corpus))
            started = agent.start(q['question'], session_id='s1', initial_result=copy.deepcopy(initial))
            row = dict(query_id=qid, paused=started['status'] == 'waiting_for_user',
                       start_status=started['status'], clarification=started.get('question'), scenarios={})
            if row['paused']:
                stored = (scratch / 'start.sqlite').read_bytes()
                for scenario, reply in replies.items():
                    db = scratch / f'{scenario}.sqlite'
                    shutil.copyfile(scratch / 'start.sqlite', db)
                    client, oracle = Recording(base), Oracle(initial['evidence'], extra, corpus)
                    done = build(db, client, oracle).reply('s1', reply)
                    cited = {x['id'] for x in done['sources']}
                    result = dict(final_status=done['status'], answer=done['text'],
                                  model_calls_after_reply=len(client.sent), follow_up_searches=len(oracle.calls))
                    if cl.get('resolved_evidence_requirements'):
                        result['cited_covers_resolved'] = [
                            any(cid in cited for cid in r['acceptable_chunk_ids'])
                            for r in cl['resolved_evidence_requirements']]
                    if scenario == 'pii_reply':
                        blob = json.dumps([client.sent, oracle.calls, done['events']], ensure_ascii=False)
                        result.update(pii_in_model_inputs_or_events=FAKE_RRN[:6] in blob,
                                      pii_in_stored_session=FAKE_RRN[:6].encode() in stored)
                    row['scenarios'][scenario] = result
        atomic_json(path, row)
        return row

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            rows = list(pool.map(run_question, questions))
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(args.output_dir / 'summary.json', rows)
    paused = [r for r in rows if r['paused']]
    print(f"questions={len(rows)} paused={len(paused)} start_statuses={dict(Counter(r['start_status'] for r in rows))}")
    unknown = [r['scenarios']['unknown_reply'] for r in paused]
    print(f"unknown_reply: needs_information={sum(x['final_status'] == 'needs_information' for x in unknown)}/{len(unknown)}"
          f" extra_model_calls={sum(x['model_calls_after_reply'] for x in unknown)}"
          f" follow_up_searches={sum(x['follow_up_searches'] for x in unknown)}")
    pii = [r['scenarios']['pii_reply'] for r in paused]
    print(f"pii_reply: responded={sum(x['final_status'] == 'responded' for x in pii)}/{len(pii)}"
          f" pii_leaks={sum(bool(x['pii_in_model_inputs_or_events'] or x['pii_in_stored_session']) for x in pii)}"
          f" statuses={dict(Counter(x['final_status'] for x in pii))}")
    human = [r['scenarios']['human_reply'] for r in paused if 'human_reply' in r['scenarios']]
    print(f"human_reply: n={len(human)} responded={sum(x['final_status'] == 'responded' for x in human)}"
          f" cited_covers_all_resolved={sum(all(x.get('cited_covers_resolved', [False])) for x in human)}")
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
