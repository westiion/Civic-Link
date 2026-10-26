"""Run/evaluate offline Hybrid+Graph and three controlled hypotheses on current Kiwi inputs."""
import argparse
import copy
from dataclasses import replace
from pathlib import Path

from civic_data import ROOT, digest, now, read_jsonl, write_json
from compare_extension import compare_files
from graph_construct import load_graph
from graph_retrieval import GraphRetriever
from hybrid_graph import Config, load_inputs, run_frozen, source_hashes
from prepare_h2 import prepare


def rebind(retriever, graph, evidence):
    if graph.graph['corpus_fingerprint'] != retriever.graph.graph['corpus_fingerprint']:
        raise ValueError('cannot reuse text index for a different corpus')
    other = copy.copy(retriever)
    other.graph, other.evidence = graph, evidence
    other.outgoing = {n: sorted([(v,k,e) for _,v,k,e in graph.out_edges(n, keys=True, data=True)],
                               key=lambda x:(x[0],x[1])) for n in graph}
    return other


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hybrid-results', type=Path, required=True)
    p.add_argument('--hybrid-run', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--experiments', nargs='+', choices=['baseline','h1','h2','h3'], default=['baseline','h1','h2','h3'])
    p.add_argument('--db', type=Path, default=ROOT/'data/processed/civic.sqlite')
    p.add_argument('--graph', type=Path, default=ROOT/'data/processed/graph.json')
    p.add_argument('--registry', type=Path, default=ROOT/'data/services.jsonl')
    p.add_argument('--queries', type=Path, default=ROOT/'data/queries.jsonl')
    p.add_argument('--questions', type=Path, default=ROOT/'data/questions.jsonl')
    a=p.parse_args()
    if a.output_dir.exists():raise ValueError('output directory exists; choose a new directory')
    config=Config()
    graph,evidence=load_graph(a.graph,a.db)
    queries,lookup,provenance=load_inputs(a.queries,a.hybrid_results,a.hybrid_run,graph,config)
    questions=read_jsonl(a.questions)
    if queries != [dict(query_id=q['question_id'],question=q['question']) for q in questions]:
        raise ValueError('evaluation question projection differs')
    a.output_dir.mkdir(parents=True)
    manifest=dict(status='running', started_at=now(), experiments=list(dict.fromkeys(a.experiments)),
                  hybrid_run=provenance, source_sha256=source_hashes(), api_calls=0, generation=None, outputs={})
    manifest_path=a.output_dir/'week4_manifest.json'
    write_json(manifest_path,manifest)
    try:
        retriever=GraphRetriever(graph,a.db,evidence)

        def execute(name, cfg, engine=retriever, graph_path=a.graph):
            path=a.output_dir/'predictions'/f'{name}.jsonl'
            print(f'Running {name} (offline)',flush=True)
            _, settings=run_frozen(engine,queries,lookup,provenance,cfg,path,graph_path)
            manifest['outputs'][name]=dict(path=str(path.relative_to(a.output_dir)),predictions_sha256=settings['predictions_sha256'])
            write_json(manifest_path,manifest)
            return path

        def comparison(kind, before, after, **kwargs):
            return compare_files(before,after,a.output_dir/'comparisons'/kind,kind=kind,db=a.db,
                                 questions=a.questions,queries=a.queries,**kwargs)

        for experiment in manifest['experiments']:
            if experiment=='baseline':
                result=execute('hybrid_graph',config)
                comparison('baseline',a.hybrid_results,result)
            elif experiment=='h1':
                before=execute('h1_lexical_uniform',replace(config,seed_mode='lexical',seed_weight='uniform'))
                after=execute('h1_hybrid_uniform',replace(config,seed_weight='uniform'))
                comparison('h1',before,after)
            elif experiment=='h2':
                directory=a.output_dir/'h2_graphs'
                prepare(a.db,a.registry,directory)
                paths={}
                graph_paths={}
                for name in ['before','after']:
                    graph_paths[name]=directory/name/'graph.json'
                    g,ev=load_graph(graph_paths[name],a.db)
                    paths[name]=execute('h2_'+name,config,rebind(retriever,g,ev),graph_paths[name])
                comparison('h2',paths['before'],paths['after'],before_graph=graph_paths['before'],after_graph=graph_paths['after'])
            else:
                before=execute('h3_rrf',config)
                after=execute('h3_feature',replace(config,rerank='feature'))
                comparison('h3',before,after)
    except Exception as error:
        manifest.update(status='failed',finished_at=now(),error_type=type(error).__name__,error=str(error))
        write_json(manifest_path,manifest)
        raise
    manifest.update(status='completed',finished_at=now())
    write_json(manifest_path,manifest)
    print(f'Completed offline Week4 experiments: {a.output_dir}',flush=True)


if __name__=='__main__':main()
