"""Gate prompt versions on the single-question sets (60 clarify, 60 answer controls, 60 abstain).

abstain questions are requests the system must hold back (formal filing, authenticity, personal lookups ...);
any question asked there is unnecessary. Clarify questions are judged against the gold rubric (Qwen judge, the
same judge prompt as run_week9_gate_scale.py). Use --prompt v3/v4 and a separate --output-dir per version.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent.clarify import GATE_PROMPTS, check_gate
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import atomic_json, identity, read_json, result_path
from run_week9_gate_scale import GATE_JUDGE, TUNED, llm_json
from run_week9_probes import ARCHIVE, BENCHMARK, WORK, initial_evidence, load_questions

GROUPS = ('clarify', 'answer_control', 'abstain')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt', choices=sorted(GATE_PROMPTS), required=True)
    parser.add_argument('--groups', nargs='+', choices=GROUPS, default=list(GROUPS))
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    output = args.output_dir or WORK / 'feedback' / f'gate-single-{args.prompt}'
    questions = load_questions(BENCHMARK / 'questions.jsonl')
    sets = {
        'clarify': [q for q in questions if q['expected_action'] == 'clarify'],
        'answer_control': sorted((q for q in questions if q['expected_action'] == 'answer'),
                                 key=lambda q: identity(['week9-gate-control-v1', q['question_id']]))[:60],
        'abstain': [q for q in questions if q['expected_action'] == 'abstain'],
    }
    items = [(g, q) for g in args.groups for q in sets[g]]
    if args.dry_run:
        print({g: len(sets[g]) for g in args.groups}, 'prompt', args.prompt, '->', output)
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    output.mkdir(parents=True, exist_ok=True)

    def run(item):
        group, q = item
        qid = q['question_id']
        path = result_path(output, group, qid)
        if path.is_file():
            return read_json(path)
        print(qid, group, flush=True)
        decision, raw = check_gate(client, q['question'], initial_evidence(qid), GATE_PROMPTS[args.prompt])
        asked = decision is not None and not decision['can_answer_now']
        row = dict(query_id=qid, group=group, tuned=qid in TUNED, gate_valid=decision is not None, asked=asked,
                   question=decision['question'] if asked else '')
        if group == 'clarify':
            row['passed'] = False
            if asked:
                rubric = q['action_rubric']
                judged = llm_json(client, output, 'judge', qid, GATE_JUDGE, dict(
                    original_question=q['question'], clarification_question=row['question'],
                    required_slots=q['clarification']['required_slots'], required=rubric.get('required'),
                    must_not=rubric.get('must_not'),
                    minimal_clarification_policy=rubric.get('minimal_clarification_policy')),
                    lambda v: isinstance(v, dict) and isinstance(v.get('asked_required'), bool)
                    and isinstance(v.get('must_not_hit'), bool))
                row['passed'] = bool(judged['value'] and judged['value']['asked_required']
                                     and not judged['value']['must_not_hit'])
        atomic_json(path, row)
        return row

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            rows = list(pool.map(run, items))
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(output / 'summary.json', rows)
    print('prompt', args.prompt)
    for group in args.groups:
        sel = [r for r in rows if r['group'] == group]
        line = f"{group} n={len(sel)} asked={sum(r['asked'] for r in sel)}"
        if group == 'clarify':
            held = [r for r in sel if not r['tuned']]
            line += f" pass={sum(r['passed'] for r in sel)} held_out_pass={sum(r['passed'] for r in held)}/{len(held)}"
        line += f" gate_invalid={sum(not r['gate_valid'] for r in sel)}"
        print(line)
    print(f'Results: {output}')


if __name__ == '__main__':
    main()
