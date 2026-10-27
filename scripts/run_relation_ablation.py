"""Compare two vs five edge types on the same frozen Hybrid results (no API)."""
import argparse
from dataclasses import replace
from pathlib import Path

from civic_data import ROOT, digest, now, read_jsonl, write_json
from compare_extension import compare_files
from graph_construct import load_graph
from graph_retrieval import GraphRetriever
from hybrid_graph import Config, LEGAL_RELATIONS, ALL_RELATIONS, load_inputs, run_frozen, source_hashes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hybrid-results', type=Path, required=True)
    p.add_argument('--hybrid-run', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--historical-two-results', type=Path)
    p.add_argument('--db', type=Path, default=ROOT/'data/processed/civic.sqlite')
    p.add_argument('--graph', type=Path, default=ROOT/'data/processed/graph.json')
    p.add_argument('--queries', type=Path, default=ROOT/'data/queries.jsonl')
    p.add_argument('--questions', type=Path, default=ROOT/'data/questions.jsonl')
    a = p.parse_args()
    if a.output_dir.exists(): raise ValueError('output directory exists; choose a new directory')
    cfg = Config()
    g, ev = load_graph(a.graph, a.db)
    queries, lookup, provenance = load_inputs(a.queries, a.hybrid_results, a.hybrid_run, g, cfg)
    questions = read_jsonl(a.questions)
    if queries != [dict(query_id=q['question_id'],question=q['question']) for q in questions]:
        raise ValueError('evaluation question projection differs')
    a.output_dir.mkdir(parents=True)
    meta = dict(status='running', started_at=now(), api_calls=0, source_sha256=source_hashes(),
                runner_sha256=digest(Path(__file__).read_bytes()), hybrid_run=provenance,
                changed_policy='allowed relations in both entry binding and traversal', outputs={})
    manifest = a.output_dir/'relation_manifest.json'
    write_json(manifest, meta)
    try:
        retriever = GraphRetriever(g, a.db, ev)
        paths = {}
        for name, relations in [('two_relations', LEGAL_RELATIONS), ('five_relations', ALL_RELATIONS)]:
            print(f'Running {name} on {len(queries)} questions (offline)', flush=True)
            paths[name] = a.output_dir/'predictions'/f'{name}.jsonl'
            rows, settings = run_frozen(retriever, queries, lookup, provenance, replace(cfg,relations=relations), paths[name], a.graph)
            meta['outputs'][name] = dict(path=str(paths[name].relative_to(a.output_dir)), sha256=settings['predictions_sha256'])
            if name == 'two_relations' and a.historical_two_results:
                old = read_jsonl(a.historical_two_results)
                if rows != old: raise ValueError('two-relation control differs from historical predictions')
                meta['historical_two_results_match'] = True
                meta['historical_two_results_sha256'] = digest(a.historical_two_results.read_bytes())
            write_json(manifest, meta)
        summaries = {}
        for label, kind, before, after in [
            ('two_vs_five', 'relations', paths['two_relations'], paths['five_relations']),
            ('hybrid_vs_two', 'baseline', a.hybrid_results, paths['two_relations']),
            ('hybrid_vs_five', 'baseline', a.hybrid_results, paths['five_relations'])]:
            print('Evaluating '+label, flush=True)
            summaries[label] = compare_files(before, after, a.output_dir/'comparisons'/label,
                kind=kind, db=a.db, questions=a.questions, queries=a.queries)
        meta.update(status='completed', finished_at=now(), comparisons=summaries)
    except Exception as error:
        meta.update(status='failed', finished_at=now(), error_type=type(error).__name__, error=str(error))
        write_json(manifest, meta)
        raise
    write_json(manifest, meta)
    print('Completed: '+str(a.output_dir), flush=True)


if __name__ == '__main__': main()
