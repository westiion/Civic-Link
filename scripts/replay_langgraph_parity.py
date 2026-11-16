"""Replay archived Qwen outputs through the original loop and the LangGraph loop (no model, no GPU).

The model is replaced by the archived raw outputs and the search tool by the archived search results;
the Graph tool is the real, deterministic one. If the replayed original loop reproduces the archived
result, the harness is faithful; if the LangGraph loop then matches too, the two implementations agree.
"""

import argparse
import copy
import json
from collections import Counter

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent import AgentConfig, RetrievalAgent
from civic_link.agent.experiment import CONDITIONS, read_json, result_path
from civic_link.agent.graph import GraphTool
from civic_link.agent.langgraph_agent import LangGraphAgent
from civic_link.agent.prompts import corpus_scope, describe_tools
from run_week9_probes import ARCHIVE, DB


class ReplayClient:
    def __init__(self, events):
        outputs = [e for e in events if e['stage'] == 'qwen_output']
        self.outputs, self.index, self.last_usage, self.seen = outputs, 0, None, []

    def complete(self, messages):
        self.seen.append(messages)
        event = self.outputs[self.index]
        self.index += 1
        self.last_usage = copy.deepcopy(event.get('usage'))
        return event['raw']


class ReplaySearch:
    def __init__(self, events):
        calls = [e['result'] for e in events if e['stage'] == 'tool_result' and not e['cached']]
        self.queue = [r for r in calls[1:] if r['tool'] == 'search']  # calls[0] is the shared first search

    def run(self, arguments):
        return copy.deepcopy(self.queue.pop(0))


def strip_times(result):
    result = copy.deepcopy(result)
    for event in result['events']:
        event.pop('seconds', None)
    return result


def first_difference(a, b):
    for key in a:
        if a[key] != b.get(key):
            if key == 'events':
                for i, (x, y) in enumerate(zip(a[key], b[key])):
                    if x != y:
                        return f'events[{i}] stage={x.get("stage")}'
                return f'events length {len(a[key])} vs {len(b[key])}'
            return key
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=60, help='questions per condition (0 = all)')
    parser.add_argument('--conditions', nargs='+', default=list(CONDITIONS), choices=list(CONDITIONS))
    args = parser.parse_args()
    config = AgentConfig(8, 6, 2, 60000)
    scope, descriptions = corpus_scope(DB), describe_tools()
    graph = GraphTool.from_files(db=DB)
    counts, problems = Counter(), []
    for condition in args.conditions:
        files = sorted((ARCHIVE / condition).glob('*.json'))
        done = 0
        for path in files:
            row = read_json(path)
            result = row['result']
            statuses = [e['result']['status'] for e in result['events'] if e['stage'] == 'tool_result']
            if result['status'] == 'model_error' or 'error' in statuses:
                counts[condition, 'skipped_error_run'] += 1
                continue
            if args.limit and done >= args.limit:
                break
            done += 1
            initial = read_json(result_path(ARCHIVE, 'initial', row['query_id']))['result']
            outcomes = {}
            for name, cls in (('original', RetrievalAgent), ('langgraph', LangGraphAgent)):
                client = ReplayClient(result['events'])
                agent = cls(client, {'search': ReplaySearch(result['events']), 'graph': graph}, scope=scope,
                            tool_descriptions=descriptions, config=config, allowed_tools=CONDITIONS[condition])
                outcomes[name] = strip_times(agent.run(row['question'], initial_result=copy.deepcopy(initial)))
            archived = strip_times(result)
            checks = dict(original_vs_archive=outcomes['original'] == archived,
                          langgraph_vs_archive=outcomes['langgraph'] == archived,
                          langgraph_vs_original=outcomes['langgraph'] == outcomes['original'])
            for key, ok in checks.items():
                counts[condition, key] += ok
            counts[condition, 'compared'] += 1
            if not all(checks.values()):
                which = 'original' if not checks['original_vs_archive'] else 'langgraph'
                problems.append((condition, row['query_id'], which, first_difference(archived, outcomes[which])))
    for condition in args.conditions:
        print(condition, {k[1]: v for k, v in counts.items() if k[0] == condition})
    for p in problems[:15]:
        print('DIFF', p)
    print('total problems', len(problems))


if __name__ == '__main__':
    main()
