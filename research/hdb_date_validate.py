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
MONTHS = {
    1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
    5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
    9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro',
}
COLLECTION = {1988: '028274_03', 1989: '028274_03', 1990: '028274_04', 1991: '028274_04'}


def sundays(year):
    current = date(year, 1, 1)
    current += timedelta(days=(6 - current.weekday()) % 7)
    while current.year == year:
        yield current
        current += timedelta(days=7)


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


async def wait_ready(page, query, timeout=18):
    deadline = time.time() + timeout
    label = ''
    while time.time() < deadline:
        label = await text(page, '#OcorNroLbl')
        current, total = parse_counter(label)
        observed = (await value(page, '#PesquisarTxt')).strip()
        if total is not None and observed == query:
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
        await asyncio.sleep(0.6)
        return (await text(page, '#OcorNroLbl')) != old_label or (await value(page, '#hPagFis')) != old_pagfis


async def new_page(context):
    page = await context.new_page()
    page.set_default_timeout(18000)
    return page


async def process_year(context, year, report, save_lock):
    bib = COLLECTION[year]
    page = await new_page(context)
    dates = list(sundays(year))
    for index, day in enumerate(dates, 1):
        query = f'1165 domingo {day.day} {MONTHS[day.month]} {day.year}'
        item = {
            'year': year,
            'date': day.isoformat(),
            'bib': bib,
            'query': query,
            'total': 0,
            'status': 'started',
            'rows': [],
            'error': '',
        }
        report['runs'].append(item)
        url = f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe="")}'
        success = False
        for attempt in range(2):
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=55000)
                await page.wait_for_selector('#OcorNroLbl', state='attached', timeout=35000)
                current, total, label = await wait_ready(page, query)
                item['total'] = total or 0
                if not total:
                    item['status'] = 'no-results'
                    success = True
                    break
                seen = set()
                limit = min(total, 100)
                for _ in range(limit + 2):
                    label = await text(page, '#OcorNroLbl')
                    current, total_now = parse_counter(label)
                    pagfis = await value(page, '#hPagFis')
                    logical_page = await value(page, '#PagAtualTxt')
                    folder = await attr(page, '#PastaTxt', 'title') or await text(page, '#PastaTxt')
                    key = (label, pagfis)
                    if key in seen:
                        break
                    seen.add(key)
                    row = {
                        'date': day.isoformat(), 'bib': bib, 'query': query,
                        'counter': label, 'folder': folder, 'page': logical_page,
                        'pagfis': pagfis,
                        'link': f'https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}',
                    }
                    item['rows'].append(row)
                    report['rows'].append(row)
                    if current is not None and total_now is not None and current >= total_now:
                        break
                    if not await advance(page, label, pagfis):
                        item['error'] = f'falha ao avançar após {label}'
                        break
                item['status'] = 'completed' if not item['error'] else 'partial'
                success = True
                break
            except Exception as exc:
                item['error'] = f'{type(exc).__name__}: {exc}'
                try:
                    await page.close()
                except Exception:
                    pass
                page = await new_page(context)
                await asyncio.sleep(1.2)
        if not success:
            item['status'] = 'error'
        if index % 12 == 0:
            try:
                await page.close()
            except Exception:
                pass
            page = await new_page(context)
        async with save_lock:
            report['summary'] = {
                'runs': len(report['runs']),
                'completed_dates': sum(1 for r in report['runs'] if r['status'] in {'completed', 'no-results'}),
                'rows': len(report['rows']),
                'errors': sum(1 for r in report['runs'] if r['status'] == 'error'),
            }
            (OUT / 'calendar_sweep.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'date': day.isoformat(), 'query': query, 'total': item['total'], 'rows': len(item['rows']), 'status': item['status'], 'error': item['error']}, ensure_ascii=False), flush=True)
    try:
        await page.close()
    except Exception:
        pass


async def async_main():
    report = {'runs': [], 'rows': [], 'summary': {}}
    save_lock = asyncio.Lock()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        contexts = []
        for _ in range(4):
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',
                locale='pt-BR', viewport={'width': 1600, 'height': 1200}, ignore_https_errors=True,
            )
            contexts.append(context)
        await asyncio.gather(*(process_year(contexts[i], year, report, save_lock) for i, year in enumerate((1988, 1989, 1990, 1991))))
        for context in contexts:
            await context.close()
        await browser.close()
    report['summary'] = {
        'runs': len(report['runs']),
        'completed_dates': sum(1 for r in report['runs'] if r['status'] in {'completed', 'no-results'}),
        'rows': len(report['rows']),
        'unique_pages': len({(r['bib'], r['pagfis']) for r in report['rows']}),
        'errors': sum(1 for r in report['runs'] if r['status'] == 'error'),
    }
    (OUT / 'calendar_sweep.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
