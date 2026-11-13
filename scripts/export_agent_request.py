"""Regenerate the published first request and lossless readable sections offline."""
import _bootstrap
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import run_agent
from civic_link.common import ROOT


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--question',default='주민등록표 등본을 대리인이 발급받으려면 어떤 서류가 필요한가요?')
    p.add_argument('--model',default='Qwen3.8-Flash-Next')
    p.add_argument('--output-dir',type=Path,default=ROOT/'week8/examples')
    a=p.parse_args(argv)
    stream=io.StringIO()
    with redirect_stdout(stream):
        run_agent.main(['--question',a.question,'--model',a.model,'--show-input'])
    body=json.loads(stream.getvalue())
    a.output_dir.mkdir(parents=True,exist_ok=True)
    (a.output_dir/'first_qwen_request.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    anchors={'역할·데이터 범위':'role-data-scope','도구와 관계 설명':'tools-and-relations','판단 지침':'decision-guidelines',
             '출력 양식':'output-format','원래 질문':'original-question','현재 실행 상태':'current-state','실행 규칙':'execution-rules'}
    block=lambda value:'```json\n'+json.dumps(value,ensure_ascii=False,indent=2)+'\n```'
    lines=['# 첫 Qwen 호출 — 7개 입력 항목 펼쳐 보기','',
        '[설계 보고서로 돌아가기](../AGENT.md#1-중앙-프로그램--첫-입력-구성) · [실제 전송 요청 JSON](first_qwen_request.json)','',
        '현재 코드에서 서버 호출 없이 생성했다. 각 content 문자열을 JSON으로 펼쳤으며 내용 생략이 없다. 전송 시 content는 문자열이다.',
        '', '## 요청 설정','',block({k:v for k,v in body.items() if k!='messages'})]
    for i,message in enumerate(body['messages']):
        lines+=['',f"## {message['role']} 메시지",'',f'원본 위치: `messages[{i}].content`']
        for title,value in json.loads(message['content']).items():
            lines+=['',f'<a id="{anchors[title]}"></a>','',f'### {title}','',block(value)]
    (a.output_dir/'first_qwen_request.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print('Updated first_qwen_request.json and first_qwen_request.md')


if __name__=='__main__':
    main()
