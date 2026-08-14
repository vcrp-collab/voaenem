import asyncio
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

OUT = Path('hdb_results')
OUT.mkdir(exist_ok=True)
MONTHS = {1:'janeiro',2:'fevereiro',3:'março',4:'abril',5:'maio',6:'junho',7:'julho',8:'agosto',9:'setembro',10:'outubro',11:'novembro',12:'dezembro'}
COLLECTION = {1988:'028274_03',1989:'028274_03',1990:'028274_04',1991:'028274_04'}


def all_sundays():
    for year in range(1988,1992):
        d=date(year,1,1)+timedelta(days=(6-date(year,1,1).weekday())%7)
        while d.year==year:
            yield d
            d+=timedelta(days=7)


def parse_counter(label):
    m=re.search(r'(\d+)\s*/\s*(\d+)',label or '')
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)


async def text(page,sel):
    try:return (await page.locator(sel).text_content(timeout=1400) or '').strip()
    except Exception:return ''


async def value(page,sel):
    try:return await page.locator(sel).get_attribute('value',timeout=1400) or ''
    except Exception:return ''


async def attr(page,sel,name):
    try:return await page.locator(sel).get_attribute(name,timeout=1400) or ''
    except Exception:return ''


async def wait_ready(page,query,timeout=15):
    end=time.time()+timeout
    label=''
    while time.time()<end:
        label=await text(page,'#OcorNroLbl')
        cur,total=parse_counter(label)
        if total is not None and (await value(page,'#PesquisarTxt')).strip()==query:
            return cur,total,label
        await asyncio.sleep(.22)
    cur,total=parse_counter(label)
    return cur,total,label


def variants(d):
    base=f'{d.day} de {MONTHS[d.month]} de {d.year}'
    return [f'"domingo, {base}"',f'"domingo {base}"']


async def one(browser,sem,d):
    bib=COLLECTION[d.year]
    result={'date':d.isoformat(),'bib':bib,'query':'','total':0,'folder':'','folder_year':None,'issue':'','page':'','pagfis':'','status':'started','error':''}
    async with sem:
        for query in variants(d):
            result['query']=query
            for attempt in range(2):
                context=await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',locale='pt-BR',viewport={'width':1500,'height':1000},ignore_https_errors=True)
                page=await context.new_page()
                try:
                    url=f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query,safe="")}'
                    await page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    await page.wait_for_selector('#OcorNroLbl',state='attached',timeout=28000)
                    cur,total,label=await wait_ready(page,query)
                    if total is None:raise RuntimeError('contador indisponível')
                    result['total']=total
                    if total==0:
                        result['status']='no-results'
                        break
                    folder=await attr(page,'#PastaTxt','title') or await text(page,'#PastaTxt')
                    result['folder']=folder
                    result['page']=await value(page,'#PagAtualTxt')
                    result['pagfis']=await value(page,'#hPagFis')
                    m=re.search(r'Ano\s+(\d{4})\\Edi(?:ç|c)ão\s+([0-9A-Z]+)',folder,re.I)
                    if m:
                        result['folder_year']=int(m.group(1));result['issue']=m.group(2)
                    result['status']='confirmed' if result['folder_year']==d.year else 'wrong-year-first'
                    break
                except Exception as exc:
                    result['error']=f'{type(exc).__name__}: {exc}'
                finally:
                    try:await page.close()
                    except Exception:pass
                    try:await context.close()
                    except Exception:pass
                if attempt==0:await asyncio.sleep(.5)
            if result['status']=='confirmed':break
            if result['status']=='wrong-year-first':break
            result['error']=''
        if result['status']=='started':result['status']='error'
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result


async def async_main():
    dates=list(all_sundays())
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        sem=asyncio.Semaphore(4)
        results=await asyncio.gather(*(one(browser,sem,d) for d in dates))
        await browser.close()
    report={'runs':results,'summary':{'dates':len(results),'confirmed':sum(r['status']=='confirmed' for r in results),'wrong_year':sum(r['status']=='wrong-year-first' for r in results),'no_results':sum(r['status']=='no-results' for r in results),'errors':sum(r['status']=='error' for r in results)}}
    (OUT/'fast_issue_map.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)


def main():asyncio.run(async_main())
if __name__=='__main__':main()
