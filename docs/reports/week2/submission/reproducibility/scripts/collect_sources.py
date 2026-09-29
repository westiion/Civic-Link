import argparse,json,time,urllib.request,urllib.error,ssl,subprocess
from pathlib import Path
from civic_data import HTMLText,decode_html,digest,now,write_json
TARGETS=[
 {'name':'seongnam','source_url':'https://www.seongnam.go.kr/cn020403','source_type':'municipal_table','required_text':'발급 가능한 민원서류'},
 {'name':'nts','source_url':'https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000263130&joTpYn=N&languageType=KO&chrClsCd=010202','source_type':'law','law_name':'국세청민원사무처리규정','effective_date':'2025-08-12','required_text':'제41조'}]

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Civic-Link-Research'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            return r.read(),r.status,r.headers.get_content_charset() or 'utf-8','urllib'
    except urllib.error.URLError as e:
        if not isinstance(e.reason,ssl.SSLError):raise
        result=subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','30','--write-out','\n%{http_code}',url],capture_output=True,timeout=35)
        if result.returncode:raise urllib.error.URLError('verified curl fallback failed') from e
        raw,status=result.stdout.rsplit(b'\n',1)
        return raw,int(status),'utf-8','curl_verified_system_trust'

def collect(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);checks=[]
    for target in TARGETS:
        check={'name':target['name'],'source_url':target['source_url'],'last_checked_at':now()}
        try:
            raw,check['http_status'],encoding,check['transport']=fetch(target['source_url'])
            p=HTMLText();p.feed(decode_html(raw,encoding))
            if target['required_text'] not in p.text:raise ValueError('HTTP success but expected body absent')
            filename=target['name']+'.html';meta_file=output/(target['name']+'.json')
            if meta_file.exists():
                old=json.loads(meta_file.read_text())
                previous=output/old['raw_filename']
                if previous.exists() and digest(previous.read_bytes())==digest(raw):
                    old['last_checked_at']=check['last_checked_at'];write_json(meta_file,old);check['status']='unchanged';checks.append(check);continue
                raise FileExistsError('changed source: choose a new dated directory to preserve existing version')
            (output/filename).write_bytes(raw)
            meta={k:v for k,v in target.items() if k not in ('required_text','name')};meta.update(raw_filename=filename,collected_at=check['last_checked_at'],last_checked_at=check['last_checked_at'],content_hash=digest(raw),http_status=check['http_status'])
            write_json(meta_file,meta);check['status']='collected'
        except (urllib.error.URLError,ValueError,FileExistsError,TimeoutError,subprocess.TimeoutExpired) as e:check['status']='failed';check['error_type']=type(e).__name__;check['reason']=str(e) if isinstance(e,(ValueError,FileExistsError)) else 'network_or_HTTP_error'
        checks.append(check);time.sleep(.5)
    write_json(output/'checks'/(now().replace(':','-')+'.json'),checks);return checks

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args();print(json.dumps(collect(a.output_dir),ensure_ascii=False,indent=2))
