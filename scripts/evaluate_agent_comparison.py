"""Score shared-query comparisons; optionally join response-bound content judgments."""
import _bootstrap
import argparse
import csv
import json
from pathlib import Path
from civic_link.agent.experiment import atomic_json, file_hash, read_json
from civic_link.agent.evaluation import evaluate_comparison


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--questions',type=Path,required=True)
    p.add_argument('--judgments',type=Path,help='directory produced by judge_agent_comparison.py')
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output_dir.exists(): p.error('use a new evaluation output directory')
    values=[json.loads(line) for line in a.questions.read_text(encoding='utf-8').splitlines() if line.strip()]
    questions={r['question_id']:r for r in values}
    if len(values)!=len(questions): p.error('duplicate evaluation question ID')
    judgments={}
    judge_config=None
    if a.judgments:
        judge_config=read_json(a.judgments/'config.json')
        if (judge_config['questions_sha256']!=file_hash(a.questions) or
                judge_config['generation_config_sha256']!=file_hash(a.run_dir/'config.json')):
            p.error('judgment configuration belongs to a different benchmark or generation run')
        for path in sorted(a.judgments.glob('*.json')):
            if path.name=='config.json': continue
            row=read_json(path)
            key=(row['condition'],row['query_id'])
            if key in judgments: p.error('duplicate judgment')
            judgments[key]=row
    report=evaluate_comparison(a.run_dir,questions,judgments)
    report['questions_sha256']=file_hash(a.questions)
    report['run_config_sha256']=file_hash(a.run_dir/'config.json')
    report['judgment_config']=judge_config
    atomic_json(a.output_dir/'report.json',report)
    with (a.output_dir/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        rows=[dict(condition=c,**v) for c,v in report['summaries'].items()]
        fields=list(dict.fromkeys(k for r in rows for k in r))
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    lines=['# Agent comparison', '',f"Paired questions: {report['paired_questions']} / {report['planned_questions']}",
           '', 'Evidence coverage and answer quality are separate. Missing content judgments are not scored as correct.',
           '', '| Condition | Final evidence complete | Content pass | Mean seconds | Model / tool calls |', '|---|---:|---:|---:|---:|']
    for c,v in report['summaries'].items():
        if not v['questions']: continue
        fmt=lambda x: 'unscored' if x is None else f'{x:.3f}'
        lines.append(f"| {c} | {fmt(v['final_complete_rate'])} | {fmt(v['content_pass_rate'])} | {v['mean_seconds']:.2f} | {v['mean_model_calls']:.2f} / {v['mean_tool_calls']:.2f} |")
    lines+=['','See report.json for paired improvements/regressions, subtask judgments, missing runs, errors and token usage.']
    (a.output_dir/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k in ('paired_questions','planned_questions','summaries')},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
