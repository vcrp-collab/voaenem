import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path('hdb_date_results')
OUT.mkdir(exist_ok=True)
MONTHS = {1:'janeiro',2:'fevereiro',3:'março',4:'abril',5:'maio',6:'junho',7:'julho',8:'agosto',9:'setembro',10:'outubro',11:'novembro',12:'dezembro'}
CANDIDATES = [
('028274_03','113223','1165 classificados','1989' if False else '1988-05-15'),
('028274_03','116617','1165 classificados','1988-08-07'),
('028274_03','117203','1165 classificados','1988-08-21'),
('028274_03','118347','1165 classificados','1988-09-18'),
('028274_03','118986','1165 classificados','1988-10-02'),
('028274_03','122987','1165 classificados','1989-01-08'),
('028274_03','123478','1165 classificados','1989-01-22'),
('028274_03','125593','1165 classificados','1989-03-19'),
('028274_03','128438','1165 classificados','1989-05-14'),
('028274_03','128639','1165 classificados','1989-05-21'),
('028274_03','128640','1165 classificados','1989-05-21'),
('028274_03','130366','1165 classificados','1989-06-25'),
('028274_03','130743','1165 classificados','1989-07-02'),
('028274_03','130751','1165 classificados','1989-07-02'),
('028274_03','135081','1165 classificados','1989-09-24'),
('028274_03','136042','11G5 classificados','1989-10-15'),
('028274_04','1078','1165 classificados','1990-01-28'),
('028274_04','18675','1165 classificados','1991-01-13'),
('028274_04','21134','1165 classificados','1991-03-03'),
('028274_04','22390','1165 classificados','1991-03-24'),
('028274_04','23151','1165 classificados','1991-04-07'),
('028274_04','23572','1165 classificados','1991-04-14'),
('028274_04','23567','11G5 classificados','1991-04-14'),
('028274_04','32139','11G5 classificados','1991-09-01'),
]

def txt(page, sel):
    try: return (page.locator(sel).text_content(timeout=2000) or '').strip()
    except Exception: return ''

def val(page, sel):
    try: return page.locator(sel).get_attribute('value',timeout=2000) or ''
    except Exception: return ''

def counter(page):
    s=txt(page,'#OcorNroLbl'); m=re.search(r'(\d+)\s*/\s*(\d+)',s)
    return (int(m.group(1)),int(m.group(2)),s) if m else (None,None,s)

def wait_ready(page, query, timeout=40):
    end=time.time()+timeout
    while time.time()<end:
        cur,total,label=counter(page)
        if total is not None and val(page,'#PesquisarTxt').strip()==query:
            return cur,total,label
        time.sleep(.35)
    return counter(page)

def advance(page, old_label, old_pf):
    try:
        page.locator('#OcorPosBtn').click(force=True,timeout=10000)
        page.wait_for_function("s=>{const a=document.querySelector('#OcorNroLbl'),b=document.querySelector('#hPagFis');return(a&&(a.textContent||'').trim()!=s.l)||(b&&(b.value||'')!=s.p)}",arg={'l':old_label,'p':old_pf},timeout=30000)
        return True
    except Exception:
        return False

def build_query(base, iso):
    y,m,d=map(int,iso.split('-'))
    return f'{base} domingo {d} {MONTHS[m]} {y}'

def main():
    report=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',locale='pt-BR',viewport={'width':1600,'height':1200},ignore_https_errors=True)
        for bib,target_pf,base,iso in CANDIDATES:
            query=build_query(base,iso)
            item={'bib':bib,'target_pagfis':target_pf,'base':base,'date':iso,'query':query,'total':0,'visited':[],'target_found':False,'status':'started','error':''}
            report.append(item)
            page=context.new_page()
            try:
                url=f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query,safe="")}'
                page.goto(url,wait_until='domcontentloaded',timeout=90000)
                page.wait_for_selector('#OcorNroLbl',state='attached',timeout=70000)
                cur,total,label=wait_ready(page,query)
                item['total']=total or 0
                seen=set()
                for _ in range((total or 0)+2):
                    cur,total_now,label=counter(page)
                    pf=val(page,'#hPagFis')
                    logical=val(page,'#PagAtualTxt')
                    folder=''
                    try: folder=page.locator('#PastaTxt').get_attribute('title',timeout=2000) or txt(page,'#PastaTxt')
                    except Exception: pass
                    key=(label,pf)
                    if key in seen: break
                    seen.add(key)
                    item['visited'].append({'counter':label,'pagfis':pf,'page':logical,'folder':folder})
                    if pf==target_pf: item['target_found']=True
                    if cur is not None and total_now is not None and cur>=total_now: break
                    if not advance(page,label,pf): break
                item['status']='confirmed' if item['target_found'] else 'not-found'
            except Exception as exc:
                item['status']='error'; item['error']=f'{type(exc).__name__}: {exc}'
            finally:
                page.close()
                (OUT/'date_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({k:item[k] for k in ['target_pagfis','date','query','total','target_found','status','error']},ensure_ascii=False),flush=True)
        browser.close()

if __name__=='__main__': main()
