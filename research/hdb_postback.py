import json
import traceback
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

OUT = Path('hdb_results')
IMG = OUT / 'images'
HTML = OUT / 'html'
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)
HTML.mkdir(exist_ok=True)

TARGET = {'bib':'028274_03','pagfis':'116342','date':'1988-07-31','issue':'09236','page':'44'}

def get_attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=5000) or ''
    except Exception:
        return ''

def main():
    report = {'target': TARGET, 'requests': [], 'responses': [], 'console': [], 'errors': [], 'steps': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36',
            locale='pt-BR', viewport={'width':1280,'height':1800},
            screen={'width':1280,'height':1800}, is_mobile=True, has_touch=True,
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        def on_request(request):
            if request.method == 'POST' and 'DocReaderMobile.aspx' in request.url:
                report['requests'].append({'method':request.method,'url':request.url,'post_data':(request.post_data or '')[:30000]})
        def on_response(response):
            if response.request.method == 'POST' and 'DocReaderMobile.aspx' in response.url:
                report['responses'].append({'method':response.request.method,'url':response.url,'status':response.status,'content_type':response.headers.get('content-type','')})
        page.on('request', on_request)
        page.on('response', on_response)
        page.on('console', lambda m: report['console'].append(f'{m.type}: {m.text}'))
        page.on('pageerror', lambda e: report['errors'].append(str(e)))

        try:
            bib, pf = TARGET['bib'], TARGET['pagfis']
            url = f'https://memoria.bn.gov.br/DocReader/DocReaderMobile.aspx?bib={bib}&PagFis={pf}&Pesq=2481165'
            page.goto(url, wait_until='domcontentloaded', timeout=90000)
            try:
                page.wait_for_selector('#form1', state='attached', timeout=90000)
            except Exception:
                page.wait_for_timeout(12000)
                page.reload(wait_until='domcontentloaded', timeout=90000)
                page.wait_for_selector('#form1', state='attached', timeout=90000)
            page.wait_for_selector('#HiddenSize', state='attached', timeout=30000)
            page.wait_for_timeout(1500)
            report['steps'].append({'step':'initial','url':page.url,'hidden_size':get_attr(page,'#HiddenSize','value'),'pagfis':get_attr(page,'#PagFisHid','value'),'documento_count':page.locator('#DocumentoImg').count()})
            (HTML/'initial.html').write_text(page.content(), encoding='utf-8')

            with page.expect_navigation(wait_until='domcontentloaded', timeout=120000):
                page.evaluate("""pf => {
                    const f=document.getElementById('form1');
                    const set=(id,v)=>{const e=document.getElementById(id); if(e)e.value=v};
                    const add=(n,v)=>{const e=document.createElement('input'); e.type='hidden'; e.name=n; e.value=v; f.appendChild(e)};
                    set('HiddenSize','1280x1718'); set('PagFisHid',pf); set('__EVENTTARGET',''); set('__EVENTARGUMENT','');
                    add('CarregaImagemHiddenButton.x','1'); add('CarregaImagemHiddenButton.y','1');
                    f.submit();
                }""", pf)
            page.wait_for_timeout(4000)
            src = get_attr(page,'#DocumentoImg','src')
            report['steps'].append({'step':'postback','url':page.url,'title':page.title(),'documento_count':page.locator('#DocumentoImg').count(),'documento_src':src,'body':(page.locator('body').inner_text(timeout=5000) or '')[:2000]})
            (HTML/'postback.html').write_text(page.content(), encoding='utf-8')
            shot=IMG/'postback.png'; page.screenshot(path=str(shot), full_page=True, timeout=90000); report['screenshot']=str(shot)
            if src:
                response=context.request.get(urljoin(page.url,src), timeout=90000, headers={'Referer':page.url})
                report['image_response']={'status':response.status,'content_type':response.headers.get('content-type',''),'url':urljoin(page.url,src)}
                if response.ok:
                    ext='.jpg' if 'jpeg' in response.headers.get('content-type','') else '.png'
                    path=IMG/f'page{ext}'; path.write_bytes(response.body()); report['image_file']=str(path)
        except Exception as exc:
            report['fatal']=f'{type(exc).__name__}: {exc}'
            report['traceback']=traceback.format_exc()
        finally:
            (OUT/'postback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
            browser.close()

if __name__ == '__main__':
    main()
