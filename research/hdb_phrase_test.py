import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

OUT = Path('hdb_results')
OUT.mkdir(exist_ok=True)

TESTS = [
    ('028274_03', '"domingo, 14 de agosto de 1988"'),
    ('028274_03', '"domingo 14 de agosto de 1988"'),
    ('028274_03', '"14 de agosto de 1988"'),
    ('028274_03', '1165 "domingo, 21 de agosto de 1988"'),
    ('028274_03', '1165 "domingo 21 de agosto de 1988"'),
    ('028274_03', '1165 "21 de agosto de 1988"'),
    ('028274_03', '1165 "domingo, 7 de agosto de 1988"'),
    ('028274_03', '1165 "7 de agosto de 1988"'),
    ('028274_04', '1165 "domingo, 28 de janeiro de 1990"'),
    ('028274_04', '1165 "28 de janeiro de 1990"'),
    ('028274_04', '1165 "domingo, 13 de janeiro de 1991"'),
    ('028274_04', '1165 "13 de janeiro de 1991"'),
]


def parse_counter(label):
    match = re.search(r'(\d+)\s*/\s*(\d+)', label or '')
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


async def text(page, selector):
    try:
        return (await page.locator(selector).text_content(timeout=1800) or '').strip()
    except Exception:
        return ''


async def value(page, selector):
    try:
        return await page.locator(selector).get_attribute('value', timeout=1800) or ''
    except Exception:
        return ''


async def attr(page, selector, name):
    try:
        return await page.locator(selector).get_attribute(name, timeout=1800) or ''
    except Exception:
        return ''


async def wait_ready(page, query, timeout=22):
    deadline = time.time() + timeout
    label = ''
    while time.time() < deadline:
        label = await text(page, '#OcorNroLbl')
        current, total = parse_counter(label)
        if total is not None and (await value(page, '#PesquisarTxt')).strip() == query:
            return current, total, label
        await asyncio.sleep(0.3)
    current, total = parse_counter(label)
    return current, total, label


async def advance(page, old_label, old_pagfis):
    try:
        await page.locator('#OcorPosBtn').click(force=True, timeout=7000)
        await page.wait_for_function(
            "s=>{const a=document.querySelector('#OcorNroLbl'),b=document.querySelector('#hPagFis');return(a&&(a.textContent||'').trim()!=s.l)||(b&&(b.value||'')!=s.p)}",
            arg={'l': old_label, 'p': old_pagfis}, timeout=15000)
        return True
    except Exception:
        return False


async def run_one(browser, semaphore, bib, query):
    result = {'bib': bib, 'query': query, 'total': 0, 'rows': [], 'status': 'started', 'error': ''}
    async with semaphore:
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',
            locale='pt-BR', viewport={'width': 1600, 'height': 1200}, ignore_https_errors=True,
        )
        page = await context.new_page()
        try:
            url = f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe="")}'
            for attempt in range(2):
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    await page.wait_for_selector('#OcorNroLbl', state='attached', timeout=40000)
                    current, total, label = await wait_ready(page, query)
                    result['total'] = total or 0
                    seen = set()
                    for _ in range(min(total or 0, 30) + 2):
                        label = await text(page, '#OcorNroLbl')
                        current, total_now = parse_counter(label)
                        pagfis = await value(page, '#hPagFis')
                        folder = await attr(page, '#PastaTxt', 'title') or await text(page, '#PastaTxt')
                        logical = await value(page, '#PagAtualTxt')
                        key = (label, pagfis)
                        if key in seen:
                            break
                        seen.add(key)
                        result['rows'].append({'counter': label, 'folder': folder, 'page': logical, 'pagfis': pagfis})
                        if current is not None and total_now is not None and current >= total_now:
                            break
                        if not await advance(page, label, pagfis):
                            result['error'] = f'falha após {label}'
                            break
                    result['status'] = 'completed' if not result['error'] else 'partial'
                    break
                except Exception as exc:
                    result['error'] = f'{type(exc).__name__}: {exc}'
                    if attempt == 0:
                        await asyncio.sleep(0.8)
                        continue
                    result['status'] = 'error'
        finally:
            await page.close()
            await context.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


async def async_main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        semaphore = asyncio.Semaphore(4)
        results = await asyncio.gather(*(run_one(browser, semaphore, bib, query) for bib, query in TESTS))
        await browser.close()
    (OUT / 'phrase_test.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'runs': len(results), 'completed': sum(1 for r in results if r['status'] == 'completed'), 'errors': sum(1 for r in results if r['status'] == 'error')}, ensure_ascii=False), flush=True)


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
