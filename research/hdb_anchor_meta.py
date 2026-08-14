import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path('hdb_anchor_results')
OUT.mkdir(exist_ok=True)
TARGETS = [
    {'bib':'028274_03','pagfis':'116880','known_date':'1988-08-14','known_page':'18'},
    {'bib':'028274_03','pagfis':'137476','known_date':'1989-11-11','known_page':'4'},
    {'bib':'028274_04','pagfis':'1263','known_date':'1990-02-02','known_page':''},
    {'bib':'028274_04','pagfis':'2938','known_date':'1990-03-15','known_page':''},
    {'bib':'028274_04','pagfis':'17822','known_date':'1990-12-26','known_page':''},
    {'bib':'028274_04','pagfis':'30078','known_date':'1991-07-27','known_page':'30'},
]

def read(page, selector, attr=None):
    try:
        loc=page.locator(selector)
        if attr:
            return loc.get_attribute(attr, timeout=3000) or ''
        return (loc.text_content(timeout=3000) or '').strip()
    except Exception:
        return ''

def main():
    report=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',locale='pt-BR',viewport={'width':1600,'height':1200},ignore_https_errors=True)
        for target in TARGETS:
            item=dict(target)
            item.update({'url':'','status':'started','folder':'','logical_page':'','pagfis_observed':'','total_pages':'','body':'','error':''})
            report.append(item)
            page=context.new_page()
            try:
                url=f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={target['bib']}&pagfis={target['pagfis']}"
                item['url']=url
                page.goto(url,wait_until='domcontentloaded',timeout=90000)
                page.wait_for_selector('#PastaTxt',state='attached',timeout=70000)
                deadline=time.time()+20
                while time.time()<deadline:
                    item['folder']=read(page,'#PastaTxt','title') or read(page,'#PastaTxt')
                    item['logical_page']=read(page,'#PagAtualTxt','value')
                    item['pagfis_observed']=read(page,'#hPagFis','value')
                    item['total_pages']=read(page,'#PagTotalLbl')
                    if item['folder'] and item['pagfis_observed']:
                        break
                    time.sleep(.4)
                item['body']=(page.locator('body').inner_text(timeout=5000) or '')[:2000]
                item['status']='ok'
            except Exception as exc:
                item['status']='error'
                item['error']=f'{type(exc).__name__}: {exc}'
            finally:
                page.close()
                (OUT/'anchors.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(item,ensure_ascii=False),flush=True)
        browser.close()

if __name__=='__main__':
    main()
