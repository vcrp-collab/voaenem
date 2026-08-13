import csv
import json
import re
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

OUT = Path("hdb_quick_results")
IMG = OUT / "images"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

COLLECTIONS = [
    {"bib": "028274_03", "years": {1988, 1989}},
    {"bib": "028274_04", "years": {1990, 1991}},
]
BASE_DATE = date(1988, 8, 14)
BASE_ISSUE = 9250


def text(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=2500) or "").strip()
    except Exception:
        return ""


def val(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=2500) or ""
    except Exception:
        return ""


def attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=2500) or ""
    except Exception:
        return ""


def counter(label):
    m = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def folder_data(title):
    ym = re.search(r"Ano\s+(\d{4})", title or "", re.I)
    em = re.search(r"Edi(?:ç|c)ão\s+(\d+)", title or "", re.I)
    return (int(ym.group(1)) if ym else None, em.group(1) if em else "")


def issue_date(issue):
    if not issue:
        return "", ""
    d = BASE_DATE + timedelta(days=int(issue) - BASE_ISSUE)
    return d.isoformat(), d.strftime("%A")


def close_modal(page):
    for sel in ['#RadWindowWrapper_PesqOpniaoRadWindow .rwCloseButton','#RadWindowWrapper_PesqOpniaoRadWindow span']:
        try:
            loc=page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click(force=True,timeout=1500)
                return
        except Exception:
            pass


def next_result(page, old_label, old_pag):
    try:
        page.locator('#OcorPosBtn').click(force=True,timeout=12000)
    except Exception:
        try: page.evaluate("document.querySelector('#OcorPosBtn').click()")
        except Exception: return False
    try:
        page.wait_for_function("s=>{let l=document.querySelector('#OcorNroLbl'),p=document.querySelector('#hPagFis');return(l&&(l.textContent||'').trim()!=s.l)||(p&&p.value!=s.p)}",arg={"l":old_label,"p":old_pag},timeout=45000)
        return True
    except Exception:
        page.wait_for_timeout(1500)
        return text(page,'#OcorNroLbl')!=old_label or val(page,'#hPagFis')!=old_pag


def save_image(page, context, stem):
    result={"image_src":"","image_file":"","errors":[]}
    try:
        page.evaluate("()=>{let p=document.getElementById('hPagFis');if(typeof PagCarrega==='function'&&p)PagCarrega(p.value);else{let b=document.getElementById('CarregaImagemHiddenButton');if(b)b.click()}}")
        page.wait_for_function("()=>{let i=document.querySelector('#DocumentoImg');return i&&i.getAttribute('src')}",timeout=50000)
    except Exception as e:
        result['errors'].append(f"load {type(e).__name__}: {e}")
    src=attr(page,'#DocumentoImg','src')
    result['image_src']=src
    if src:
        try:
            response=context.request.get(urljoin(page.url,src),timeout=60000,headers={"Referer":page.url})
            if response.ok:
                ctype=response.headers.get('content-type','')
                ext='.jpg' if 'jpeg' in ctype else '.png' if 'png' in ctype else '.bin'
                path=IMG/f"{stem}{ext}"
                path.write_bytes(response.body())
                result['image_file']=str(path)
            else: result['errors'].append(f"HTTP {response.status}")
        except Exception as e: result['errors'].append(f"download {type(e).__name__}: {e}")
    return result


def main():
    output={"started":time.time(),"runs":[],"rows":[],"errors":[]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',locale='pt-BR',viewport={"width":1800,"height":1300},ignore_https_errors=True)
        page=context.new_page()
        for collection in COLLECTIONS:
            bib=collection['bib']
            url=f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq=2481165"
            run={"bib":bib,"url":url,"status":"started","counter":"","visited":0,"matched":0,"error":""}
            output['runs'].append(run)
            print(f"=== {bib} ===",flush=True)
            try:
                page.goto(url,wait_until='domcontentloaded',timeout=90000)
                close_modal(page)
                page.wait_for_selector('#OcorNroLbl',timeout=60000)
                label=text(page,'#OcorNroLbl')
                cur,total=counter(label)
                run['counter']=label
                print('counter',label,flush=True)
                if not total or total>300: raise RuntimeError(f"unexpected counter {label}")
                for _ in range(total):
                    label=text(page,'#OcorNroLbl')
                    n,t=counter(label)
                    folder=attr(page,'#PastaTxt','title')
                    year,issue=folder_data(folder)
                    pagfis=val(page,'#hPagFis')
                    pageno=val(page,'#PagAtualTxt')
                    d,weekday=issue_date(issue)
                    row={"bib":bib,"counter":label,"year":year,"issue":issue,"date":d,"weekday":weekday,"is_sunday":weekday=='Sunday',"page":pageno,"pagfis":pagfis,"folder":folder,"link":f"https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}" if pagfis else page.url,"image_file":"","image_src":"","errors":[]}
                    run['visited']+=1
                    if year in collection['years']:
                        run['matched']+=1
                        if row['is_sunday']:
                            row.update(save_image(page,context,f"{bib}_{year}_{issue}_{pagfis}_p{pageno}"))
                        output['rows'].append(row)
                        print(json.dumps(row,ensure_ascii=False),flush=True)
                    if n is not None and n>=total: break
                    if not next_result(page,label,pagfis):
                        run['error']=f"advance failed at {label}"
                        break
                run['status']='completed' if not run['error'] else 'partial'
            except Exception as e:
                run['status']='error';run['error']=f"{type(e).__name__}: {e}"
                output['errors'].append({"bib":bib,"error":run['error'],"traceback":traceback.format_exc()})
                print(traceback.format_exc(),flush=True)
            (OUT/'quick.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
    output['finished']=time.time()
    (OUT/'quick.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'quick.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['bib','year','issue','date','weekday','is_sunday','page','pagfis','counter','link','image_file','image_src']
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(output['rows'])

if __name__=='__main__': main()
