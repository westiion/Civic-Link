"""Post-selection explanatory ablations; never reselect the frozen policy."""

import _bootstrap  # Make the local src package available to script entry points.
import argparse
from pathlib import Path
import copy
import json
from collections import Counter
from civic_link.common import ROOT, connect, read_jsonl, write_json
from civic_link.reranking.policies import stored_order
from civic_link.reranking.coverage import rank
from run_score_comparison import metrics


def run(output, baseline):
    output=Path(output)
    selected=json.loads((output/'selection.json').read_text(encoding='utf-8'))['selected']
    assert selected=='score_text_priority_overlap'
    qs=read_jsonl(ROOT/'data/benchmark/questions.jsonl')
    core={q['question_id'] for q in read_jsonl(ROOT/'data/benchmark/diagnostic480/questions.jsonl')}
    split=json.loads((output/'protocol.json').read_text(encoding='utf-8'))['split']
    rows=read_jsonl(baseline)
    with connect(ROOT/'data/corpus/civic.sqlite',True) as db:
        chunks={r['chunk_id']:dict(r) for r in db.execute('select * from chunks')}
    names=('selected','no_graph_same_weights','no_overlap','no_part_of_discount','no_hop_discount')
    orders={n:{} for n in names}
    for row in rows:
        for name in names:
            candidates=row['candidates']
            if name in ('no_part_of_discount','no_hop_discount'):
                candidates=copy.deepcopy(candidates)
                for c in candidates:
                    for path in c['paths']:
                        if name=='no_part_of_discount':
                            for edge in path['path_edges']:
                                if edge['relation']=='PART_OF':edge['relation']='REFERENCES'
                        else:path['path_edges']=[];path['graph_hops']=0
            order,_=rank(candidates,chunks,weights=(.3,.5,.2),
                         use_graph=name!='no_graph_same_weights',penalty=0 if name=='no_overlap' else .15)
            orders[name][row['query_id']]=order
    summary={'purpose':'post-selection explanatory diagnostics; no validation-driven reselection','selected':selected,'subsets':{}}
    baseline={r['query_id']:stored_order(r) for r in rows}
    orders['h3_feature']=baseline
    for subset in ('all','balanced','development','validation'):
        subset_q=qs if subset=='all' else [q for q in qs if (q['question_id'] in core if subset=='balanced' else split[q['question_id']]['split']==subset)]
        summary['subsets'][subset]={name:metrics(subset_q,order)[0] for name,order in orders.items()}
    summary['balanced_by_type']={}
    for kind in sorted({q['question_type'] for q in qs if q['expected_action']=='answer'}):
        subset_q=[q for q in qs if q['question_id'] in core and q['question_type']==kind]
        summary['balanced_by_type'][kind]={name:metrics(subset_q,orders[name])[0] for name in ('h3_feature','selected')}
    changes=read_jsonl(output/'changes.jsonl')
    lookup={q['question_id']:q for q in qs}
    summary['regressions']=[dict(question_id=r['question_id'],question=lookup[r['question_id']]['question'],
        split=r['split'],old_ranks=r['previous_group_ranks'],new_ranks=r['group_ranks']) for r in changes if not r['10']['all_evidence']]
    summary['ranking_change']={}
    for subset in ('all','balanced'):
        ids=[q['question_id'] for q in qs if subset=='all' or q['question_id'] in core]
        summary['ranking_change'][subset]=dict(n=len(ids),same_top10_order=sum(baseline[q][:10]==orders['selected'][q][:10] for q in ids),
            same_top10_set=sum(set(baseline[q][:10])==set(orders['selected'][q][:10]) for q in ids))
    write_json(output/'explanatory_ablation.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--h3',type=Path,default=ROOT/'outputs/baseline/h3.jsonl')
    args=parser.parse_args()
    run(args.output_dir,args.h3)
