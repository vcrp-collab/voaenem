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


def sundays(year):
    current=date(year,1,1)+timedelta(days=(6-date(year,1,1).weekday())%7)
    while current.year==year:
        yield current
        current+=timedelta(days=7)


def parse_counter(label):
    match=re.search(r'(\d+)\s*/\s*(\d+)',label or '')
    return (int(match.group(1)),int(match.group(2))) if match else (None,None)


async def text(page,selector):
    try:return (await page.locator(selector).text_content(timeout=1400) or '').strip()
    except Exception:return ''


async def value(page,selector):
    try:return await page.locator(selector).get_attribute('value',timeout=1400) or ''
    except Exception:return ''


async def attr(page,selector,name):
    try:return await page.locator(selector).get_attribute(name,timeout=1400) or ''
    except Exception:return ''


async def wait_ready(page,query,timeout=14):
    deadline=time.time()+timeout
    label=''
    while time.time()<deadline:
        label=await text(page,'#OcorNroLbl')
        current,total=parse_counter(label)
        if total is not None and (await value(page,'#PesquisarTxt')).strip()==query:
            return current,total,label
        await asyncio.sleep(.2)
    current,total=parse_counter(label)
    return current,total,label


def variants(day):
    base=f'{day.day} de {MONTHS[day.month]} de {day.year}'
    return [f'"domingo, {base}"',f'"domingo {base}"']


async def create_session(browser):
    context=await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',locale='pt-BR',viewport={'width':1500,'height':1000},ignore_https_errors=True)
    page=await context.new_page()
    page.set_default_timeout(16000)
    return context,page


async def worker(browser,year,shared,lock):
    bib=COLLECTION[year]
    context,page=await create_session(browser)
    for index,day in enumerate(sundays(year),1):
        item={'date':day.isoformat(),'bib':bib,'query':'','total':0,'folder':'','folder_year':None,'issue':'','page':'','pagfis':'','status':'started','error':''}
        for query in variants(day):
            item['query']=query
            for attempt in range(2):
                try:
                    url=f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query,safe="")}'
                    await page.goto(url,wait_until='domcontentloaded',timeout=42000)
                    await page.wait_for_selector('#OcorNroLbl',state='attached',timeout=26000)
                    current,total,label=await wait_ready(page,query)
                    if total is None:raise RuntimeError('contador indisponível')
                    item['total']=total
                    if total==0:
                        item['status']='no-results'
                        break
                    folder=await attr(page,'#PastaTxt','title') or await text(page,'#PastaTxt')
                    item['folder']=folder
                    item['page']=await value(page,'#PagAtualTxt')
                    item['pagfis']=await value(page,'#hPagFis')
                    match=re.search(r'Ano\s+(\d{4})\\Edi(?:ç|c)ão\s+([0-9A-Z]+)',folder,re.I)
                    if match:
                        item['folder_year']=int(match.group(1));item['issue']=match.group(2)
                    item['status']='confirmed' if item['folder_year']==year else 'wrong-year-first'
                    break
                except Exception as exc:
                    item['error']=f'{type(exc).__name__}: {exc}'
                    try:await page.close()
                    except Exception:pass
                    try:await context.close()
                    except Exception:pass
                    context,page=await create_session(browser)
                    await asyncio.sleep(.5)
            if item['status'] in {'confirmed','wrong-year-first'}:break
            item['error']=''
        if item['status']=='started':item['status']='error'
        async with lock:
            shared.append(item)
            report={'runs':sorted(shared,key=lambda row:row['date']),'summary':{'dates':len(shared),'confirmed':sum(row['status']=='confirmed' for row in shared),'wrong_year':sum(row['status']=='wrong-year-first' for row in shared),'no_results':sum(row['status']=='no-results' for row in shared),'errors':sum(row['status']=='error' for row in shared)}}
            (OUT/'fast_issue_map.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(item,ensure_ascii=False),flush=True)
        if index%15==0:
            try:await page.close()
            except Exception:pass
            try:await context.close()
            except Exception:pass
            context,page=await create_session(browser)
    try:await page.close()
    except Exception:pass
    try:await context.close()
    except Exception:pass


async def async_main():
    shared=[]
    lock=asyncio.Lock()
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        await asyncio.gather(*(worker(browser,year,shared,lock) for year in (1988,1989,1990,1991)))
        await browser.close()
    report={'runs':sorted(shared,key=lambda row:row['date']),'summary':{'dates':len(shared),'confirmed':sum(row['status']=='confirmed' for row in shared),'wrong_year':sum(row['status']=='wrong-year-first' for row in shared),'no_results':sum(row['status']=='no-results' for row in shared),'errors':sum(row['status']=='error' for row in shared)}}
    (OUT/'fast_issue_map.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)


def main():asyncio.run(async_main())
if __name__=='__main__':main()
