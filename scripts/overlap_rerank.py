"""Gold-free, fixed-pool suppression of overlapping source spans after H3."""
import math


def span_overlap(a, b):
    """Overlap coefficient on source offsets, never titles or gold labels."""
    if a['document_id'] != b['document_id']:
        return 0.0
    if min(a['end_char']-a['start_char'], b['end_char']-b['start_char']) <= 0:
        raise ValueError('invalid source span')
    return max(0, min(a['end_char'],b['end_char'])-max(a['start_char'],b['start_char'])) / min(
        a['end_char']-a['start_char'],b['end_char']-b['start_char'])


def rerank_overlap(candidates, chunks, *, penalty=.15, top_k=10):
    """Only chunk_id/fusion_rank/fusion_score and source offsets enter scoring.

    Select the first top_k greedily; retain original order in the remaining tail.
    No query IDs, question types, service labels or relevance judgments are read.
    """
    if not math.isfinite(penalty) or penalty < 0 or type(top_k) is not int or top_k < 1:
        raise ValueError('invalid penalty or top_k')
    rows=sorted((c for c in candidates if c['fusion_rank'] is not None),key=lambda c:c['fusion_rank'])
    if [c['fusion_rank'] for c in rows] != list(range(1,len(rows)+1)):
        raise ValueError('invalid ranks')
    order=[c['chunk_id'] for c in rows]
    if len(set(order))!=len(order) or any(cid not in chunks for cid in order):
        raise ValueError('duplicate or unknown chunk')
    scores={c['chunk_id']:c['fusion_score'] for c in rows}
    if any(not math.isfinite(s) or s<0 for s in scores.values()):
        raise ValueError('invalid score')
    maximum=max(scores.values(),default=1.) or 1.
    selected=[]; traces=[]
    for _ in range(min(top_k,len(order))):
        options=[]
        for rank,cid in enumerate(order,1):
            if cid in selected:
                continue
            overlap=max((span_overlap(chunks[cid],chunks[s]) for s in selected),default=0.)
            value=scores[cid]/maximum-penalty*overlap
            options.append((value,-rank,cid,overlap))
        value,_,cid,overlap=max(options)
        selected.append(cid)
        traces.append(dict(chunk_id=cid,base_score=scores[cid]/maximum,
                           maximum_selected_span_overlap=overlap,selection_score=value))
    return selected+[cid for cid in order if cid not in selected],traces
