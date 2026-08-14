import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

OUT = Path('hdb_results')
OUT.mkdir(exist_ok=True)

MONTHS = {
    1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
    5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
    9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro',
}

DATES = [
    ('1988-05-15', '028274_03', '1165'),
    ('1988-08-07', '028274_03', '1165'),
    ('1988-08-21', '028274_03', '1165'),
    ('1988-09-18', '028274_03', '1165'),
    ('1988-10-02', '028274_03', '1165'),
    ('1989-01-08', '028274_03', '1165'),
    ('1989-01-22', '028274_03', '1165'),
    ('1989-03-19', '028274_03', '1165'),
    ('1989-05-14', '028274_03', '1165'),
    ('1989-05-21', '028274_03', '1165'),
    ('1989-06-25', '028274_03', '1165'),
    ('1989-07-02', '028274_03', '1165'),
    ('1989-09-24', '028274_03', '1165'),
    ('1989-10-15', '028274_03', '11G5'),
    ('1990-01-28', '028274_04', '1165'),
    ('1991-01-13', '028274_04', '1165'),
    ('1991-03-03', '028274_04', '1165'),
    ('1991-03-24', '028274_04', '1165'),
    ('1991-04-07', '028274_04', '1165'),
    ('1991-04-14', '028274_04', '1165'),
    ('1991-04-14', '028274_04', '11G5'),
    ('1991-09-01', '028274_04', '11G5'),
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


async def wait_ready(page, query, timeout=20):
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
        await page.locator('#OcorPosBtn').click(force=True, timeout=8000)
        await page.wait_for_function(
            "s=>{const a=document.querySelector('#OcorNroLbl'),b=document.querySelector('#hPagFis');return(a&&(a.textContent||'').trim()!=s.l)||(b&&(b.value||'')!=s.p)}",
            arg={'l': old_label, 'p': old_pagfis},
            timeout=18000,
        )
        return True
    except Exception:
        await asyncio.sleep(0.5)
        return (await text(page, '#OcorNroLbl')) != old_label or (await value(page, '#hPagFis')) != old_pagfis


def query_for(iso_date, token):
    year, month, day = map(int, iso_date.split('-'))
    return f'{token} domingo {day} {MONTHS[month]} {year}'


async def process_one(context, semaphore, iso_date, bib, token):
    query = query_for(iso_date, token)
    item = {
        'date': iso_date, 'bib': bib, 'token': token, 'query': query,
        'total': 0, 'rows': [], 'status': 'started', 'error': '',
    }
    async with semaphore:
        page = await context.new_page()
        page.set_default_timeout(18000)
        try:
            url = f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe="")}'
            for attempt in range(2):
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    await page.wait_for_selector('#OcorNroLbl', state='attached', timeout=40000)
                    current, total, label = await wait_ready(page, query)
                    item['total'] = total or 0
                    if not total:
                        item['status'] = 'no-results'
                        break
                    seen = set()
                    for _ in range(min(total, 50) + 2):
                        label = await text(page, '#OcorNroLbl')
                        current, total_now = parse_counter(label)
                        pagfis = await value(page, '#hPagFis')
                        logical_page = await value(page, '#PagAtualTxt')
                        folder = await attr(page, '#PastaTxt', 'title') or await text(page, '#PastaTxt')
                        key = (label, pagfis)
                        if key in seen:
                            break
                        seen.add(key)
                        item['rows'].append({
                            'date': iso_date, 'bib': bib, 'token': token, 'query': query,
                            'counter': label, 'folder': folder, 'page': logical_page,
                            'pagfis': pagfis,
                            'link': f'https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}',
                        })
                        if current is not None and total_now is not None and current >= total_now:
                            break
                        if not await advance(page, label, pagfis):
                            item['error'] = f'falha ao avançar após {label}'
                            break
                    item['status'] = 'completed' if not item['error'] else 'partial'
                    break
                except Exception as exc:
                    item['error'] = f'{type(exc).__name__}: {exc}'
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    item['status'] = 'error'
        finally:
            await page.close()
    print(json.dumps({'date': iso_date, 'token': token, 'total': item['total'], 'rows': len(item['rows']), 'status': item['status'], 'error': item['error']}, ensure_ascii=False), flush=True)
    return item


async def async_main():
    report = {'runs': [], 'rows': [], 'summary': {}}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',
            locale='pt-BR', viewport={'width': 1600, 'height': 1200}, ignore_https_errors=True,
        )
        semaphore = asyncio.Semaphore(4)
        runs = await asyncio.gather(*(process_one(context, semaphore, *entry) for entry in DATES))
        report['runs'] = runs
        report['rows'] = [row for run in runs for row in run['rows']]
        report['summary'] = {
            'runs': len(runs),
            'completed': sum(1 for run in runs if run['status'] in {'completed', 'no-results'}),
            'errors': sum(1 for run in runs if run['status'] == 'error'),
            'rows': len(report['rows']),
            'unique_pages': len({(row['bib'], row['pagfis']) for row in report['rows']}),
        }
        (OUT / 'fast_date_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
        await context.close()
        await browser.close()


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
