"""Separate explicit action, citation syntax and post-hoc rubric diagnostics."""
import _bootstrap  # Make local src imports available.
import argparse
from collections import Counter
import json
from pathlib import Path
from civic_link.common import read_jsonl, write_json, write_jsonl, ROOT, connect, digest, input_bytes, input_exists, input_path, read_json
from civic_link.evaluation.behavior import evaluate_behavior
from run_week7_experiment2 import QUESTIONS,CORE,DB
from run_week7_behavior import METHODS


def normalize(row):
    result=dict(row)
    try:
        value=json.loads(row['raw_response'])
        if value.get('action') not in ('answer','clarify','abstain'):raise ValueError('missing action')
        if not isinstance(value.get('response'),str) or not value['response'].strip():raise ValueError('missing response')
        if not isinstance(value.get('questions'),list) or any(not isinstance(x,str) for x in value['questions']):raise ValueError('invalid questions')
    except (ValueError,TypeError) as exc:
        result.update(predicted_action=None,response=None,clarification_questions=None,action_parse_error=str(exc));return result
    citations=value.get('cited_sources')
    valid=isinstance(citations,list) and all(type(x)==int and 1<=x<=10 for x in citations)
    result.update(predicted_action=value['action'],response=value['response'],clarification_questions=value['questions'],cited_sources=citations,action_parse_error=None,citation_syntax_valid=valid)
    return result


def content_pass(judgment):
    return (judgment['required_handling_ok'] and not judgment['unsupported_claim']
            and judgment.get('factual_correct') is not False)


def normalize_judgment(row,expected_action):
    """Preserve field-level missingness instead of discarding other valid verdicts."""
    result=dict(row)
    try:
        j=json.loads(row['raw_judgment'])
        required={'required_handling_ok','unsupported_claim','action_response_consistent',
            'clarification_sufficient','factual_correct','grounded_in_provided_passages',
            'reason','response_excerpt','failed_requirements'}
        if set(j)!=required:raise ValueError('invalid judgment fields')
        for k in ('required_handling_ok','unsupported_claim','action_response_consistent'):
            if type(j[k]) is not bool:raise ValueError('invalid boolean')
        for k in ('clarification_sufficient','factual_correct','grounded_in_provided_passages'):
            if j[k] is not None and type(j[k]) is not bool:raise ValueError('invalid nullable boolean')
        if any(not isinstance(j[k],str) or not j[k].strip() for k in ('reason','response_excerpt')):raise ValueError('missing explanation')
        if not isinstance(j['failed_requirements'],list) or any(not isinstance(x,str) for x in j['failed_requirements']):raise ValueError('invalid requirement list')
    except (ValueError,TypeError):
        result['judgment']=None;result['normalized_parse_error']='invalid structural judgment';return result
    result['judgment']=j;result['normalized_parse_error']=None
    result['field_warnings']=(['missing_clarification_verdict'] if expected_action=='clarify' and j['clarification_sufficient'] is None else
        ['irrelevant_clarification_verdict'] if expected_action!='clarify' and j['clarification_sufficient'] is not None else [])
    return result


def content_metrics(pairs,include_verbatim=True):
    valid=[(q,j) for q,j in pairs if j is not None]
    count=len(valid)
    result=dict(total=len(pairs),measured=count,missing=len(pairs)-count,
        content_pass=sum(content_pass(j['judgment']) for _,j in valid) if count else None,
        required_handling_ok=sum(j['judgment']['required_handling_ok'] for _,j in valid) if count else None,
        unsupported_claim=sum(j['judgment']['unsupported_claim'] for _,j in valid) if count else None,
        action_response_consistent=sum(j['judgment']['action_response_consistent'] for _,j in valid) if count else None,
        nonverbatim_excerpts=sum(not j['excerpt_is_verbatim'] for _,j in valid) if count else None)
    result['content_pass_rate']=result['content_pass']/count if count else None
    for name in ('clarification_sufficient','factual_correct','grounded_in_provided_passages'):
        selected=[j['judgment'][name] for q,j in valid if j['judgment'].get(name) is not None
                  and (name!='clarification_sufficient' or q.get('expected_action')=='clarify')]
        result[name]=dict(measured=len(selected),true=sum(selected) if selected else None,
            rate=sum(selected)/len(selected) if selected else None)
    if include_verbatim:
        result['verbatim_excerpt_subset']=content_metrics([(q,j if j is not None and j['excerpt_is_verbatim'] else None) for q,j in pairs],False)
    return result


def summarize(directory,destination,require_complete=True):
    questions=read_jsonl(QUESTIONS);mapping=read_json((directory/'input_mapping.json'))
    with connect(DB,True) as db:titles={r['chunk_id']:r['title'] for r in db.execute('select chunk_id,title from chunks')}
    raw=read_jsonl(directory/'responses.jsonl');cache={r['input_key']:normalize(r) for r in raw}
    if len(raw)!=len(cache):raise ValueError('duplicate cached inputs')
    expected={r['input_key'] for m in mapping.values() for r in m.values()}
    if require_complete and set(cache)!=expected:raise ValueError('generation incomplete')
    judge_path=directory/'content_judge/judgments.jsonl'
    if not input_exists(judge_path):judge_path=directory/'nonanswer_judge/judgments.jsonl'
    judge_rows=read_jsonl(judge_path) if input_exists(judge_path) else []
    question_map={q['question_id']:q for q in questions}
    if judge_path.parent.name=='content_judge':
        judge_rows=[normalize_judgment(r,question_map[r['query_id']]['expected_action']) for r in judge_rows]
        for r in judge_rows:
            if r['judgment'] is not None:
                excerpt=r['judgment']['response_excerpt']
                r['excerpt_is_verbatim']=bool(excerpt) and excerpt in cache[r['input_key']]['response']
    judged={(r['query_id'],r['input_key']):r for r in judge_rows}
    if input_exists(judge_path) and judge_path.parent.name=='content_judge':
        judge_run=read_json((judge_path.parent/'run.json'))
        if require_complete and judge_run['status']!='completed':raise ValueError('post-hoc judgment incomplete')
        judge_config=read_json((judge_path.parent/'config.json'))
        if judge_config['generation_sha256']!=digest(input_bytes((directory/'responses.jsonl'))):raise ValueError('judged generations changed')
        if judge_config['questions_sha256']!=digest(input_bytes(QUESTIONS)):raise ValueError('judged reference changed')
    split=read_json((ROOT/'experiments/score_comparison/results/protocol.json'))['split']
    subsets=dict(all=questions,balanced=read_jsonl(CORE))
    subsets.update({s:[q for q in questions if split[q['question_id']]['split']==s] for s in ('development','validation')})
    review_path=ROOT/'week7/evaluation/experiment2/content_review.json'
    review_notes=read_json(review_path)['cases'] if review_path.exists() else []
    cases={};summary={}
    for method in METHODS:
        rows=[]
        for q in questions:
            qid=q['question_id'];ref=mapping[method][qid]
            if ref['input_key'] not in cache:continue
            row={'query_id':qid,**ref,**cache[ref['input_key']]};rows.append(row)
            cases.setdefault(qid,{})[method]={k:row.get(k) for k in ('predicted_action','response','clarification_questions','cited_sources','citation_syntax_valid','action_parse_error','finish_reason')}
            if row['action_parse_error']:
                cases[qid][method]['raw_response_on_parse_error']=row['raw_response']
            cases[qid][method]['acceptable_action_match']=row['predicted_action'] in q.get('acceptable_actions',[q['expected_action']]) if row['predicted_action'] else None
            cases[qid][method]['provided_chunk_ids']=ref['retrieved_chunk_ids']
            citations=row.get('cited_sources')
            cases[qid][method]['cited_source_titles']={str(i):titles[ref['retrieved_chunk_ids'][i-1]] for i in citations if type(i)==int and 1<=i<=len(ref['retrieved_chunk_ids'])} if isinstance(citations,list) else {}
            if (qid,ref['input_key']) in judged:
                j=judged[qid,ref['input_key']]
                if j.get('judgment') is not None:
                    cases[qid][method]['rubric_diagnostic']=dict(judgment=j['judgment'],excerpt_is_verbatim=j['excerpt_is_verbatim'],content_pass=content_pass(j['judgment']),field_warnings=j.get('field_warnings',[]))
                else:cases[qid][method]['content_judgment_error']=j.get('parse_error','invalid judge output')
            notes=[{k:v for k,v in note.items() if k not in ('question_id','methods')} for note in review_notes if note['question_id']==qid and method in note['methods']]
            if notes:cases[qid][method]['content_review_annotations']=notes
        valid=[r for r in rows if r['predicted_action'] is not None]
        summary[method]={}
        for name,qs in subsets.items():
            ids={q['question_id'] for q in qs};predictions=[r for r in valid if r['query_id'] in ids]
            metrics,_=evaluate_behavior(qs,predictions)
            matrix=metrics['confusion_matrix']
            metrics['abstain_on_answer']=dict(count=matrix['answer']['abstain'],denominator=metrics['by_expected_action']['answer']['measured_count'])
            metrics['answer_on_abstain']=dict(count=matrix['abstain']['answer'],denominator=metrics['by_expected_action']['abstain']['measured_count'])
            metrics['citation_syntax_invalid_count']=sum(not r['citation_syntax_valid'] for r in predictions)
            lookup={r['query_id']:r for r in predictions}
            clarify_rows=[lookup[q['question_id']] for q in qs if q['expected_action']=='clarify' and q['question_id'] in lookup]
            metrics['clarification_output_structure']=dict(
                measured=len(clarify_rows),has_at_least_one_question=sum(bool(r['clarification_questions']) for r in clarify_rows),
                scope='presence_only_not_semantic_slot_coverage')
            js=[(q,cases.get(q['question_id'],{}).get(method,{}).get('rubric_diagnostic')) for q in qs if q['expected_action']!='answer']
            measured=[(q,j) for q,j in js if j is not None]
            metrics['rubric_diagnostic']=dict(scope='same_Qwen_model_posthoc_not_human_or_independent_judge',measured=len(measured),total=len(js),
                required_handling_ok=sum(j['judgment']['required_handling_ok'] for _,j in measured) if measured else None,
                unsupported_claim=sum(j['judgment']['unsupported_claim'] for _,j in measured) if measured else None,
                nonverbatim_excerpts=sum(not j['excerpt_is_verbatim'] for _,j in measured) if measured else None,
                clarification_measured=sum(q['expected_action']=='clarify' and j['judgment']['clarification_sufficient'] is not None for q,j in measured),
                clarification_sufficient=sum(q['expected_action']=='clarify' and j['judgment']['clarification_sufficient'] is True for q,j in measured) if measured else None)
            all_judgments=[(q,cases.get(q['question_id'],{}).get(method,{}).get('rubric_diagnostic')) for q in qs]
            metrics['content_diagnostic']=content_metrics(all_judgments)
            metrics['content_diagnostic']['by_expected_action']={a:content_metrics([(q,j) for q,j in all_judgments if q['expected_action']==a]) for a in ('answer','clarify','abstain')}
            metrics['generation_quality']=dict(status='model_diagnostic_only' if metrics['content_diagnostic']['measured'] else 'unmeasured',
                accuracy=None,automatic_diagnostic_measured=metrics['content_diagnostic']['measured'],
                scope='No independently verified answer accuracy; see separate content_diagnostic and review warnings.')
            summary[method][name]=metrics
    config=read_json((directory/'config.json'))
    shared_config={k:v for k,v in config.items() if k not in ('endpoint','queries_sha256','rankings_sha256','code_sha256')}
    shared_config['seed']=7
    result=dict(config=shared_config,unique_inputs=len(expected),completed_inputs=len(cache),question_count=len(cases),
        input_reuse=4*608-len(expected),methods=summary,raw_action_parse_errors=sum(r['action_parse_error'] is not None for r in cache.values()),
        citation_syntax_invalid_unique=sum(r.get('citation_syntax_valid') is False for r in cache.values()),
        sum_request_seconds=sum(r['seconds'] for r in cache.values()),
        token_usage={k:sum((r.get('usage') or {}).get(k,0) for r in cache.values()) for k in ('prompt_tokens','completion_tokens','total_tokens')},
        generation_content_accuracy='same_model_posthoc_rubric_diagnostic_not_independent_accuracy' if judged else 'unmeasured_no_content_judgments')
    if judge_rows and judge_path.parent.name=='content_judge':
        result['posthoc_judgment']=dict(
            config={k:v for k,v in judge_config.items() if k not in ('generation_sha256','questions_sha256')},
            run=judge_run,valid_unique=sum(r['judgment'] is not None for r in judge_rows),
            structurally_invalid_unique=sum(r['judgment'] is None for r in judge_rows),
            nonverbatim_unique=sum(r['judgment'] is not None and not r['excerpt_is_verbatim'] for r in judge_rows),
            field_warning_counts=dict(Counter(w for r in judge_rows for w in r.get('field_warnings',[]))),
            token_usage={k:sum((r.get('usage') or {}).get(k,0) for r in judge_rows) for k in ('prompt_tokens','completion_tokens','total_tokens')},
            definitions=dict(content_pass='required_handling_ok AND NOT unsupported_claim AND factual_correct is not false; model diagnostic only',
                grounding='reported separately; reference evidence is not treated as evidence provided at generation',
                missing='invalid original generation or invalid/missing judge output; never imputed as pass or failure',
                partial_fields='a null clarification verdict on a clarify item stays missing for that field; other structurally valid verdicts remain measured; raw runner parse warnings are retained',
                excerpt='exact string match to original response; non-matches retained and flagged; no verdict correction',
                bias='same model judges its fixed generations; no independent human or external fact verification'))
    result['bge_formula_input_comparison']=dict(
        identical_inputs=sum(mapping['h3_bge'][q['question_id']]['input_key']==mapping['bge'][q['question_id']]['input_key'] for q in questions),
        different_input_questions=[q['question_id'] for q in questions if mapping['h3_bge'][q['question_id']]['input_key']!=mapping['bge'][q['question_id']]['input_key']])
    result['paired_action_changes']={}
    result['paired_verified_content_changes']={}
    for before,after in [('h3','coverage'),('coverage','bge'),('h3','h3_bge'),('h3_bge','bge')]:
        comparisons={}
        for name,qs in subsets.items():
            improved=[];worsened=[];changed=[];measured=0
            for q in qs:
                qid=q['question_id'];a=cases.get(qid,{}).get(before,{});b=cases.get(qid,{}).get(after,{})
                x=a.get('acceptable_action_match');y=b.get('acceptable_action_match')
                if x is None or y is None:continue
                measured+=1
                if not x and y:improved.append(qid)
                if x and not y:worsened.append(qid)
                if a['predicted_action']!=b['predicted_action']:changed.append(qid)
            comparisons[name]=dict(measured=measured,improved=improved,worsened=worsened,changed_action=changed)
        result['paired_action_changes'][before+'->'+after]=comparisons
        content_comparisons={}
        for name,qs in subsets.items():
            improved=[];worsened=[];measured=0
            for q in qs:
                qid=q['question_id'];a=cases.get(qid,{}).get(before,{}).get('rubric_diagnostic');b=cases.get(qid,{}).get(after,{}).get('rubric_diagnostic')
                if not a or not b or not a['excerpt_is_verbatim'] or not b['excerpt_is_verbatim']:continue
                measured+=1;x=a['content_pass'];y=b['content_pass']
                if not x and y:improved.append(qid)
                if x and not y:worsened.append(qid)
            content_comparisons[name]=dict(measured=measured,improved=improved,worsened=worsened,
                scope='same-model judgments with exact excerpts for both compared responses; not independently validated content')
        result['paired_verified_content_changes'][before+'->'+after]=content_comparisons
    result['common_verbatim_content_subset']={}
    for name,qs in subsets.items():
        common=[q for q in qs if all(cases.get(q['question_id'],{}).get(m,{}).get('rubric_diagnostic',{}).get('excerpt_is_verbatim') for m in METHODS)]
        result['common_verbatim_content_subset'][name]=dict(question_count=len(common),source_question_count=len(qs),
            question_ids=[q['question_id'] for q in common],
            methods={m:content_metrics([(q,cases[q['question_id']][m]['rubric_diagnostic']) for q in common],False) for m in METHODS})
    result['completion_diagnostics']=dict(
        finish_reasons=dict(Counter(r.get('finish_reason','unknown') for r in cache.values())),
        retried_requests=sum(r.get('attempts',1)>1 for r in cache.values()),
        max_attempts=max((r.get('attempts',1) for r in cache.values()),default=0))
    segment_path=directory/'segment_workers4.json'
    current_run=read_json((directory/'run.json'))
    previous_run=read_json(segment_path) if segment_path.exists() else None
    result['execution_timing']=dict(
        completed_request_seconds=result['sum_request_seconds'],
        recorded_client_loop_wall_seconds=current_run['current_invocation_wall_seconds']+(previous_run['current_invocation_wall_seconds'] if previous_run else 0),
        scope='client loop wall intervals exclude initial four-input smoke, setup/restart gap and unfinished requests discarded on restart; request seconds sum includes completed smoke requests',
        workers_sequence=[4,8] if previous_run else [config['workers']],
        preserved_before_restart=previous_run['saved_count_verified'] if previous_run else None)
    destination.mkdir(parents=True,exist_ok=True);write_json(destination/'behavior_summary.json',result);write_json(destination/'behavior_cases.json',cases)
    if judge_rows and judge_path.parent.name=='content_judge':
        validation=[]
        for r in judge_rows:
            qid=r['query_id']
            validation.append(dict(question_id=qid,methods=[m for m in METHODS if mapping[m][qid]['input_key']==r['input_key']],
                original_runner_parse_error=r.get('parse_error'),structural_valid=r['judgment'] is not None,
                field_warnings=r.get('field_warnings',[]),excerpt_is_verbatim=r['excerpt_is_verbatim'] if r['judgment'] is not None else None))
        write_json(destination/'content_validation.json',dict(
            scope='deterministic validation of fixed raw judgments; no new inference and no verdict override',
            inference_calls_for_reprocessing=0,original_generation_unchanged=True,
            rule='All structurally valid fields retained. Null clarification verdicts on clarify items remain missing only for that field. Non-clarify slot verdicts excluded from slot metrics. Exact response excerpts checked independently.',
            records=validation))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--behavior-dir',type=Path,required=True);p.add_argument('--summary-dir',type=Path,required=True);p.add_argument('--allow-partial',action='store_true')
    a=p.parse_args();r=summarize(a.behavior_dir,a.summary_dir,not a.allow_partial);print(json.dumps({k:r[k] for k in ('unique_inputs','completed_inputs','question_count','raw_action_parse_errors')},indent=2))
