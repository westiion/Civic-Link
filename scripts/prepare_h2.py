"""Build a paired evidence-repair ablation, without retrieval or API calls."""
import argparse
from pathlib import Path
from civic_data import ROOT, digest, write_json
from graph_construct import build_graph, save_graph, load_graph


def compare(before, after):
    bg, be = before
    ag, ae = after
    if dict(bg.nodes(data=True)) != dict(ag.nodes(data=True)):
        raise ValueError('unexpected node changes')
    if {(u,v,k):d for u,v,k,d in bg.edges(keys=True,data=True)} != {(u,v,k):d for u,v,k,d in ag.edges(keys=True,data=True)}:
        raise ValueError('unexpected edge changes')
    if any(ae['records'].get(k) != v for k,v in be['records'].items()):
        raise ValueError('existing evidence changed or removed')
    additions=[]
    for kind in ('node', 'edge'):
        old,new=be[kind+'_bindings'],ae[kind+'_bindings']
        for owner in set(old)|set(new):
            if not set(old.get(owner,[])) <= set(new.get(owner,[])):
                raise ValueError('binding removed')
            for eid in sorted(set(new.get(owner,[]))-set(old.get(owner,[]))):
                additions.append(dict(kind=kind,owner_id=owner,evidence_id=eid,record=ae['records'][eid]))
    if not additions:
        raise ValueError('repair produced no change')
    allowed={'scoped_exact_cell_alias','scoped_negative_statement'}
    if any(x['record']['extraction_rule'] not in allowed for x in additions):
        raise ValueError('unexpected repair type')
    return sorted(additions,key=lambda x:(x['kind'],x['owner_id'],x['evidence_id']))


def prepare(db, registry, output_dir):
    db, registry, output_dir = Path(db), Path(registry), Path(output_dir)
    if output_dir.exists():
        raise ValueError('output directory exists; choose a new directory')
    graphs={}
    unresolved={}
    for name, enabled in [('before',False),('after',True)]:
        g,ev,unresolved[name]=build_graph(db,registry,scoped_repairs=enabled)
        graphs[name]=(g,ev)
    additions=compare(graphs['before'],graphs['after'])
    if unresolved['before']!=unresolved['after']:
        raise ValueError('reference extraction unexpectedly changed')
    output_dir.mkdir(parents=True)
    artifacts={}
    for name,(g,ev) in graphs.items():
        target=output_dir/name/'graph.json'
        target.parent.mkdir()
        save_graph(g,ev,target)
        load_graph(target,db)
        artifacts[name]=dict(graph_sha256=digest(target.read_bytes()),
            evidence_sha256=digest(target.with_suffix('.evidence.json').read_bytes()),
            nodes=len(g),edges=g.number_of_edges(),evidence_records=len(ev['records']))
    write_json(output_dir/'repair_diff.json',dict(scope='scoped alias and negative evidence bindings only; current legal reference parser unchanged',
        additions=additions,removed_bindings=0,changed_domain_edges=0,
        human_review_status='pending',corpus_fingerprint=graphs['before'][0].graph['corpus_fingerprint']))
    write_json(output_dir/'build_manifest.json',dict(artifacts=artifacts,
        database_sha256=digest(db.read_bytes()),registry_sha256=digest(registry.read_bytes()),
        builder_sha256=digest((ROOT/'scripts/graph_construct.py').read_bytes()),
        preparation_sha256=digest(Path(__file__).read_bytes()),api_calls=0))
    return artifacts


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=ROOT/'data/processed/civic.sqlite')
    p.add_argument('--registry',type=Path,default=ROOT/'data/services.jsonl')
    p.add_argument('--output-dir',type=Path,default=ROOT/'data/processed/h2')
    a=p.parse_args()
    prepare(a.db,a.registry,a.output_dir)
    print(f'Prepared before/after graphs at {a.output_dir}. No retrieval run.')


if __name__=='__main__':
    main()
