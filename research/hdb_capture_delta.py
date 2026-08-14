import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT=Path('hdb_delta_results'); OUT.mkdir(exist_ok=True)
TARGETS=[
 {'bib':'028274_03','pagfis':'117203','query':'1165'},
 {'bib':'028274_03','pagfis':'116617','query':'1165'},
 {'bib':'028274_04','pagfis':'1078','query':'1165'},
]

def main():
 report=[]
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  context=browser.new_context(user_agent='Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36',locale='pt-BR',viewport={'width':1280,'height':1800},is_mobile=True,has_touch=True,ignore_https_errors=True)
  for target in TARGETS:
   item=dict(target); item.update({'url':'','responses':[],'cache_urls':[],'error':''}); report.append(item)
   page=context.new_page()
   def on_response(resp, item=item):
    if resp.request.method=='POST' and 'DocReaderMobile.aspx' in resp.url:
     rec={'status':resp.status,'content_type':resp.headers.get('content-type',''),'url':resp.url,'file':'','length':0,'error':''}
     item['responses'].append(rec)
     try:
      body=resp.body()
      rec['length']=len(body)
      path=OUT/f"delta_{item['bib']}_{item['pagfis']}_{len(item['responses'])}.bin"
      path.write_bytes(body); rec['file']=str(path)
      text=body.decode('utf-8','ignore')
      urls=re.findall(r'(?:https?:)?//[^\s\"\'<>|]+|(?:\.\./|/)?cache/[^\s\"\'<>|]+',text,re.I)
      for u in urls:
       if u not in item['cache_urls']: item['cache_urls'].append(u)
     except Exception as exc:
      rec['error']=f'{type(exc).__name__}: {exc}'
   page.on('response',on_response)
   try:
    url=f"https://memoria.bn.gov.br/DocReader/DocReaderMobile.aspx?bib={target['bib']}&PagFis={target['pagfis']}&Pesq={target['query']}"
    item['url']=url
    page.goto(url,wait_until='domcontentloaded',timeout=90000)
    page.wait_for_timeout(18000)
    item['body']=(page.locator('body').inner_text(timeout=5000) or '')[:3000]
    item['html']=page.content()[:10000]
   except Exception as exc:
    item['error']=f'{type(exc).__name__}: {exc}'
   finally:
    page.close(); (OUT/'capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(item,ensure_ascii=False),flush=True)
  browser.close()
if __name__=='__main__': main()
