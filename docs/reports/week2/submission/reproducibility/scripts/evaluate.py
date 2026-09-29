import math
from collections import defaultdict

KS=(1,3,5,10)
CAUSES=['DATA_MISSING','VOCABULARY_MISMATCH','EXACT_TERM_MISS','SEMANTIC_CONFUSION','CHUNKING_ERROR','TABLE_PARSING_ERROR','MULTI_HOP_REQUIRED','OUT_OF_SCOPE','UNANSWERABLE']

def evaluate(questions,predictions,known_ids):
    gold={q['question_id']:q for q in questions};pred={p['query_id']:p for p in predictions}
    if not gold or len(gold)!=len(questions) or len(pred)!=len(predictions):raise ValueError('empty or duplicate question IDs')
    if set(pred)-set(gold):raise ValueError('unknown prediction query ID')
    for q in questions:
        if q['expected_answerability'] not in ('answerable','unanswerable'):raise ValueError('unknown answerability')
        if q['expected_answerability']=='answerable' and not q['evidence_requirements']:raise ValueError('answerable without references')
        for e in q['evidence_requirements']:
            if not set(e['acceptable_chunk_ids'])<=known_ids:raise ValueError('unknown gold chunk')
    for p in predictions:
        ids=[h['chunk_id'] for h in p['hits']]
        if len(ids)!=len(set(ids)) or not set(ids)<=known_ids:raise ValueError('unknown or duplicate predicted chunk')
        for i,h in enumerate(p['hits'],1):
            if h['rank']!=i or h['query_id']!=p['query_id'] or h['retriever']!=p['retriever'] or not math.isfinite(h['score']):raise ValueError('invalid prediction rank/identity/score')
    metrics=[];cases=[]
    for qid,q in gold.items():
        hits=pred.get(qid,{}).get('hits',[]);ranked=[h['chunk_id'] for h in hits];requirements=q['evidence_requirements'];groups=[set(e['acceptable_chunk_ids']) for e in requirements]
        row={'query_id':qid,'question':q['question'],'question_type':q['question_type'],'document_hops':q['document_hops'],'challenge_tags':q['challenge_tags'],'answerability':q['expected_answerability']}
        if q['expected_answerability']=='answerable':
            row['metrics']={}
            for k in KS:
                found=[bool(g&set(ranked[:k])) for g in groups]
                row['metrics'][str(k)]={'recall':sum(found)/len(groups),'hit':int(any(found)),'all_evidence':int(all(found)),'mrr':next((1/(i+1) for i,c in enumerate(ranked[:k]) if any(c in g for g in groups)),0)}
            metrics.append(row)
            missed=[e for e,g in zip(requirements,groups) if not g&set(ranked[:10])]
            status='success' if not missed else 'partial' if any(g&set(ranked[:10]) for g in groups) else 'failure'
            candidates=[]
            if any(not e['acceptable_chunk_ids'] for e in missed):candidates.append('DATA_MISSING')
            if missed and q['document_hops'] and q['document_hops']>=2:candidates.append('MULTI_HOP_REQUIRED')
            if missed and 'table_and_note' in q['challenge_tags']:candidates.append('TABLE_PARSING_ERROR')
            if missed and 'everyday_language' in q['challenge_tags']:candidates.append('VOCABULARY_MISMATCH')
            if missed and q['question_type'] in ('legal_grounding','cross_reference'):candidates.append('EXACT_TERM_MISS')
            if missed and 'similar_service_contrast' in q['challenge_tags']:candidates.append('SEMANTIC_CONFUSION')
            if missed and any(e.get('document_id') in {h['document_id'] for h in hits} for e in missed):candidates.append('CHUNKING_ERROR')
        else:
            missed=[];status='not_scored_unanswerable';candidates=['OUT_OF_SCOPE' if q['unanswerable_reason']=='OUT_OF_SCOPE' else 'UNANSWERABLE']
        cases.append({**row,'retrieval_status_at_10':status,'missing_evidence':[{'source_alias':e['source_alias'],'locator':e['locator'],'source_url':e['source_url']} for e in missed],'cause_candidates':candidates,'confirmed_cause':None,'analysis_status':'hypothesis_pending_human_review','review_note':'Candidates are inferred from question tags and missing locators, not proven causes. Inspect raw citation and ranked text.','top_10':hits,'abstention_basis':q['abstention_basis']})
    def aggregate(rows):
        return {'question_count':len(rows),'metrics':{str(k):{key:sum(r['metrics'][str(k)][key] for r in rows)/len(rows) if rows else None for key in ['recall','hit','all_evidence','mrr']} for k in KS}}
    def grouped(field,multi=False):
        out=defaultdict(list)
        for r in metrics:
            for key in r[field] if multi else [r[field]]:out[str(key)].append(r)
        return {k:aggregate(v) for k,v in sorted(out.items())}
    return {'question_count':len(gold),'prediction_count':len(pred),'missing_predictions':sorted(set(gold)-set(pred)),'retrieval':{'overall':aggregate(metrics),'by_type':grouped('question_type'),'by_hop':grouped('document_hops'),'by_challenge':grouped('challenge_tags',True)},'granularity':'article_or_guide_section_relevance_not_claim_entailment','human_gold_review':'pending','answerability_accuracy':None,'generation_accuracy':None},cases
