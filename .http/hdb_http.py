import csv
import json
import re
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT=Path('hdb_http_results'); OUT.mkdir(exist_ok=True)
COLLECTIONS=[('028274_03',{1988,1989}),('028274_04',{1990,1991})]
BASE_ISSUE=9250; BASE_DATE=date(1988,8,14)
HEADERS={
 'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36',
 'Accept-Language':'pt-BR,pt;q=0.9,en;q=0.8',
}

def soup(html): return BeautifulSoup(html,'html.parser')

def field(s,id):
    e=s.find(id=id)
    return (e.get('value') or '') if e else ''

def text(s,id):
    e=s.find(id=id)
    return e.get_text(' ',strip=True) if e else ''

def title(s,id):
    e=s.find(id=id)
    return (e.get('title') or '') if e else ''

def count(label):
    m=re.search(r'(\d+)\s*/\s*(\d+)',label or '')
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def metadata(s):
    folder=title(s,'PastaTxt')
    ym=re.search(r'Ano\s+(\d{4})',folder,re.I)
    em=re.search(r'Edi(?:ç|c)ão\s+(\d+)',folder,re.I)
    year=int(ym.group(1)) if ym else None
    issue=em.group(1) if em else ''
    if issue:
        d=BASE_DATE+timedelta(days=int(issue)-BASE_ISSUE)
        ds=d.isoformat(); wd=d.strftime('%A')
    else: ds=wd=''
    return {
      'counter':text(s,'OcorNroLbl'),'folder':folder,'year':year,'issue':issue,
      'date':ds,'weekday':wd,'is_sunday':wd=='Sunday','page':field(s,'PagAtualTxt'),
      'pagfis':field(s,'hPagFis'),'search_value':field(s,'PesquisarTxt'),
      'acervo':text(s,'AcervoDescLbl'),
    }

def payload(s,button='OcorPosBtn'):
    form=s.find('form')
    data={}
    for e in form.find_all(['input','select','textarea']):
        name=e.get('name')
        if not name: continue
        typ=(e.get('type') or '').lower()
        if typ in {'submit','button','image','file','reset'}: continue
        if typ in {'checkbox','radio'} and not e.has_attr('checked'): continue
        if e.name=='select':
            opt=e.find('option',selected=True) or e.find('option')
            data[name]=(opt.get('value') if opt else '') or ''
        elif e.name=='textarea': data[name]=e.get_text()
        else: data[name]=e.get('value') or ''
    data['__EVENTTARGET']=''; data['__EVENTARGUMENT']=''
    data[f'{button}.x']='1'; data[f'{button}.y']='1'
    return data, urljoin('https://memoria.bn.gov.br/DocReader/',form.get('action') or '')

def main():
    report={'started':time.time(),'runs':[],'rows':[],'errors':[]}
    for bib,years in COLLECTIONS:
        run={'bib':bib,'status':'started','counter':'','visited':0,'matched':0,'error':''}
        report['runs'].append(run)
        session=requests.Session(); session.headers.update(HEADERS)
        url=f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq=2481165'
        print('\n===',bib,'===',flush=True)
        try:
            r=session.get(url,timeout=60); r.raise_for_status(); current_url=r.url
            s=soup(r.text); first=metadata(s); run['counter']=first['counter']
            n,total=count(first['counter'])
            print('GET',r.status_code,len(r.text),first,flush=True)
            if not total or total>300: raise RuntimeError(f'unexpected counter {first["counter"]}')
            seen=set()
            for step in range(total):
                m=metadata(s); n,total_now=count(m['counter']); run['visited']+=1
                key=(m['pagfis'],m['counter'])
                if key in seen: raise RuntimeError(f'loop detected {key}')
                seen.add(key)
                if m['year'] in years:
                    m['bib']=bib; m['link']=f'https://memoria.bn.gov.br/DocReader/{bib}/{m["pagfis"]}'
                    report['rows'].append(m); run['matched']+=1
                    print(json.dumps(m,ensure_ascii=False),flush=True)
                if n is not None and n>=total: break
                data,post_url=payload(s)
                r=session.post(post_url,data=data,headers={'Referer':current_url},timeout=75)
                r.raise_for_status(); current_url=r.url; s=soup(r.text)
                new=metadata(s)
                if new['counter']==m['counter'] and new['pagfis']==m['pagfis']:
                    raise RuntimeError(f'postback did not advance at {m["counter"]}, status {r.status_code}, len {len(r.text)}')
            run['status']='completed'
        except Exception as e:
            run['status']='error'; run['error']=f'{type(e).__name__}: {e}'
            report['errors'].append({'bib':bib,'error':run['error'],'traceback':traceback.format_exc()})
            print(traceback.format_exc(),flush=True)
        (OUT/'http.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['finished']=time.time(); (OUT/'http.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['bib','year','issue','date','weekday','is_sunday','page','pagfis','counter','folder','link']
    with (OUT/'http.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(report['rows'])
    print(json.dumps({'runs':report['runs'],'rows':len(report['rows'])},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
