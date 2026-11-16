"""LangGraph clarification flow end to end with a real Qwen on the 5 gold-resolved clarify questions.

Flow per question: first-search evidence (archived) -> gate question -> pause -> Qwen "user" answers from the
hidden resolved situation -> resume from a NEW agent object and a reopened SQLite checkpoint -> follow-up
search -> answer. The follow-up search is an ORACLE here (the first-search evidence plus the gold chunks of the
resolved requirements) because live retrieval needs the BGE/embedding stack. The result is therefore an upper
bound for "gate + pause/resume + a follow-up search that works", not a retrieval result.
Compare with run_week9_gate_scale.py resume (answer from the saved first-search evidence only).
"""

import argparse
import copy
import json
import os
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent import AgentConfig
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import atomic_json, read_json, result_path
from civic_link.agent.index import load_corpus
from civic_link.agent.langgraph_agent import LangGraphAgent
from civic_link.agent.prompts import corpus_scope, describe_tools
from run_week9_gate_scale import RESUME_JUDGE, USER_SIM, llm_json
from run_week9_probes import ARCHIVE, BENCHMARK, DB, WORK, load_questions

OUT = WORK / 'feedback' / 'langgraph-resume'
KEYS = ('id', 'document_id', 'title', 'text', 'source')


class OracleSearch:
    def __init__(self, first, extra_ids, corpus):
        rows = {r['id']: r for r in first}
        rows.update({cid: corpus[cid] for cid in extra_ids})
        self.rows = [{k: r[k] for k in KEYS} for r in rows.values()]

    def run(self, arguments):
        return {'tool': 'search', 'status': 'ok', 'evidence': copy.deepcopy(self.rows), 'relations': []}


class Unused:
    def run(self, arguments):
        raise RuntimeError('graph is disabled in this experiment')


def main():
    from langgraph.checkpoint.sqlite import SqliteSaver
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    questions = [q for q in load_questions(BENCHMARK / 'questions.jsonl')
                 if q['expected_action'] == 'clarify' and 'simulated_user_response' in (q.get('clarification') or {})]
    corpus = {r['id']: r for r in load_corpus(DB)}
    if args.dry_run:
        print(len(questions), 'cases:', [q['question_id'] for q in questions])
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scope, descriptions, config = corpus_scope(DB), describe_tools(), AgentConfig(8, 6, 2, 60000)
    db_path = str(args.output_dir / 'checkpoints.sqlite')
    rows = []
    try:
        for q in questions:
            qid, cl = q['question_id'], q['clarification']
            path = result_path(args.output_dir, 'run', qid)
            if path.is_file():
                rows.append(read_json(path))
                continue
            print(qid, flush=True)
            initial = read_json(result_path(ARCHIVE, 'initial', qid))['result']
            extra = [cid for req in cl['resolved_evidence_requirements'] for cid in req['acceptable_chunk_ids']]
            oracle = OracleSearch(initial['evidence'], extra, corpus)
            started = perf_counter()

            def make(saver):
                return LangGraphAgent(client, {'search': oracle, 'graph': Unused()}, scope=scope,
                                      tool_descriptions=descriptions, config=config, allowed_tools=('search',),
                                      checkpointer=saver, use_gate=True)
            thread = f'{qid}-run'
            with SqliteSaver.from_conn_string(db_path) as saver:
                waiting = make(saver).run(q['question'], initial_result=initial, thread_id=thread)
            row = dict(query_id=qid, paused=waiting.get('status') == 'waiting_for_user')
            if row['paused']:
                sim = llm_json(client, args.output_dir, 'user', qid, USER_SIM,
                               dict(original_question=q['question'], hidden_situation=cl['resolved_question'],
                                    clarification_question=waiting['question']),
                               lambda v: isinstance(v, dict) and isinstance(v.get('reply'), str) and v['reply'].strip())
                reply = (sim['value'] or {}).get('reply')
                row.update(clarification=waiting['question'], simulated_reply=reply)
                if reply:
                    with SqliteSaver.from_conn_string(db_path) as saver:  # new connection, new agent object
                        done = make(saver).resume(thread, reply)
                    row.update(final_status=done['status'], answer=done['text'], tool_calls=done['tool_calls'],
                               model_calls=done['model_calls'], cited=[s['id'] for s in done['sources']],
                               evidence_ids=[e['id'] for e in done['evidence']])
                    judged = llm_json(client, args.output_dir, 'judge', qid, RESUME_JUDGE,
                                      dict(situation=cl['resolved_question'], gold=cl['resolved_gold_answer'],
                                           answer=done['text']),
                                      lambda v: isinstance(v, dict) and isinstance(v.get('matches_gold'), bool)
                                      and isinstance(v.get('unsupported_claim'), bool))
                    row.update(matches_gold=(judged['value'] or {}).get('matches_gold'),
                               unsupported_claim=(judged['value'] or {}).get('unsupported_claim'))
            else:
                row.update(final_status=waiting.get('status'), answer=waiting.get('text'))
            row['seconds'] = perf_counter() - started
            atomic_json(path, row)
            rows.append(row)
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(args.output_dir / 'summary.json', rows)
    print(f"n={len(rows)} paused={sum(r['paused'] for r in rows)}"
          f" responded={sum(r.get('final_status') == 'responded' for r in rows)}"
          f" matches_gold={sum(bool(r.get('matches_gold')) for r in rows)}"
          f" unsupported_claim={sum(bool(r.get('unsupported_claim')) for r in rows)}")
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
