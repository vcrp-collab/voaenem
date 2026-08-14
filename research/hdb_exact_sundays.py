import asyncio
import json
import re
import time
from collections import Counter
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


def sundays(start_year=1988, end_year=1991):
    for year in range(start_year, end_year + 1):
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
        return (await page.locator(selector).text_content(timeout=1600) or '').strip()
    except Exception:
        return ''


async def value(page, selector):
    try:
        return await page.locator(selector).get_attribute('value', timeout=1600) or ''
    except Exception:
        return ''


async def attr(page, selector, name):
    try:
        return await page.locator(selector).get_attribute(name, timeout=1600) or ''
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
        await asyncio.sleep(0.25)
    current, total = parse_counter(label)
    return current, total, label


async def advance(page, old_label, old_pagfis):
    try:
        await page.locator('#OcorPosBtn').click(force=True, timeout=6500)
        await page.wait_for_function(
            "s=>{const a=document.querySelector('#OcorNroLbl'),b=document.querySelector('#hPagFis');return(a&&(a.textContent||'').trim()!=s.l)||(b&&(b.value||'')!=s.p)}",
            arg={'l': old_label, 'p': old_pagfis}, timeout=12000)
        return True
    except Exception:
        return False


def phrase_variants(day):
    base = f'{day.day} de {MONTHS[day.month]} de {day.year}'
    return [f'"domingo, {base}"', f'"domingo {base}"']


async def search_phrase(browser, semaphore, day):
    bib = COLLECTION[day.year]
    item = {
        'date': day.isoformat(), 'bib': bib, 'weekday': 'Sunday',
        'status': 'started', 'query': '', 'total': 0, 'rows': [],
        'issues': [], 'selected_issue': '', 'error': '',
    }
    async with semaphore:
        for variant_index, query in enumerate(phrase_variants(day)):
            item['query'] = query
            for attempt in range(2):
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',
                    locale='pt-BR', viewport={'width': 1600, 'height': 1200}, ignore_https_errors=True,
                )
                page = await context.new_page()
                page.set_default_timeout(16000)
                try:
                    url = f'https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe="")}'
                    await page.goto(url, wait_until='domcontentloaded', timeout=55000)
                    await page.wait_for_selector('#OcorNroLbl', state='attached', timeout=35000)
                    current, total, label = await wait_ready(page, query)
                    if total is None:
                        raise RuntimeError('contador não ficou pronto')
                    item['total'] = total
                    if total == 0:
                        item['status'] = 'no-results'
                        break
                    seen = set()
                    limit = min(total, 8)
                    for _ in range(limit + 1):
                        label = await text(page, '#OcorNroLbl')
                        current, total_now = parse_counter(label)
                        pagfis = await value(page, '#hPagFis')
                        logical = await value(page, '#PagAtualTxt')
                        folder = await attr(page, '#PastaTxt', 'title') or await text(page, '#PastaTxt')
                        marker = (label, pagfis)
                        if marker in seen:
                            break
                        seen.add(marker)
                        match = re.search(r'Ano\s+(\d{4})\\Edi(?:ç|c)ão\s+([0-9A-Z]+)', folder, re.I)
                        issue = match.group(2) if match else ''
                        folder_year = int(match.group(1)) if match else None
                        item['rows'].append({
                            'counter': label, 'folder': folder, 'folder_year': folder_year,
                            'issue': issue, 'page': logical, 'pagfis': pagfis,
                            'link': f'https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}',
                        })
                        if current is not None and total_now is not None and current >= total_now:
                            break
                        if not await advance(page, label, pagfis):
                            break
                    target_issues = [row['issue'] for row in item['rows'] if row['folder_year'] == day.year and row['issue']]
                    if target_issues:
                        counts = Counter(target_issues)
                        item['issues'] = sorted(counts)
                        item['selected_issue'] = counts.most_common(1)[0][0]
                        item['status'] = 'confirmed'
                        await page.close(); await context.close()
                        print(json.dumps({k: item[k] for k in ['date','query','total','issues','selected_issue','status']}, ensure_ascii=False), flush=True)
                        return item
                    item['status'] = 'ambiguous'
                    break
                except Exception as exc:
                    item['error'] = f'{type(exc).__name__}: {exc}'
                finally:
                    try: await page.close()
                    except Exception: pass
                    try: await context.close()
                    except Exception: pass
                if attempt == 0:
                    await asyncio.sleep(0.8)
            if item['status'] == 'confirmed':
                break
            if variant_index == 0:
                item['rows'] = []
                item['issues'] = []
                item['selected_issue'] = ''
                item['error'] = ''
    if item['status'] not in {'confirmed', 'no-results', 'ambiguous'}:
        item['status'] = 'error'
    print(json.dumps({k: item[k] for k in ['date','query','total','issues','selected_issue','status','error']}, ensure_ascii=False), flush=True)
    return item


async def async_main():
    dates = list(sundays())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        semaphore = asyncio.Semaphore(4)
        results = await asyncio.gather(*(search_phrase(browser, semaphore, day) for day in dates))
        await browser.close()
    report = {
        'runs': results,
        'confirmed': [item for item in results if item['status'] == 'confirmed'],
        'summary': {
            'dates_tested': len(results),
            'confirmed_dates': sum(1 for item in results if item['status'] == 'confirmed'),
            'no_results': sum(1 for item in results if item['status'] == 'no-results'),
            'ambiguous': sum(1 for item in results if item['status'] == 'ambiguous'),
            'errors': sum(1 for item in results if item['status'] == 'error'),
        },
    }
    (OUT / 'exact_sunday_issue_map.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)


def main():
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
