import csv
import json
import re
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright

BASE = "https://memoria.bn.gov.br/DocReader/DocReader.aspx"
OUT = Path("hdb_results")
IMG = OUT / "images"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

COLLECTIONS = [
    {"bib": "028274_03", "label": "Correio Braziliense (DF) - 1980 a 1989", "years": [1988, 1989]},
    {"bib": "028274_04", "label": "Correio Braziliense (DF) - 1990 a 1998", "years": [1990, 1991]},
]

# The HDB index treats '-' as a broad separator, so the literal hyphen query is
# unusably large. These forms cover punctuation removal plus common OCR errors.
SEARCHES = [
    {"name": "joined", "query": "2481165", "tier": 1},
    {"name": "quoted-space", "query": '"248 1165"', "tier": 1},
    {"name": "space", "query": "248 1165", "tier": 1},
    {"name": "joined-l165", "query": "248l165", "tier": 2},
    {"name": "joined-1l65", "query": "2481l65", "tier": 2},
    {"name": "joined-ll65", "query": "248ll65", "tier": 2},
    {"name": "joined-I165", "query": "248I165", "tier": 2},
    {"name": "joined-116S", "query": "248116S", "tier": 2},
    {"name": "joined-I16S", "query": "248I16S", "tier": 2},
    {"name": "joined-ll6S", "query": "248ll6S", "tier": 2},
    {"name": "joined-Z48", "query": "Z481165", "tier": 2},
    {"name": "joined-24B", "query": "24B1165", "tier": 2},
    {"name": "quoted-l165", "query": '"248 l165"', "tier": 2},
    {"name": "quoted-1l65", "query": '"248 1l65"', "tier": 2},
    {"name": "quoted-ll65", "query": '"248 ll65"', "tier": 2},
    {"name": "quoted-I165", "query": '"248 I165"', "tier": 2},
    {"name": "quoted-116S", "query": '"248 116S"', "tier": 2},
    {"name": "quoted-Z48", "query": '"Z48 1165"', "tier": 2},
    {"name": "quoted-24B", "query": '"24B 1165"', "tier": 2},
]

BASE_ISSUE = 9250
BASE_DATE = date(1988, 8, 14)  # independently documented Correio issue/date


def safe_text(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=2500) or "").strip()
    except Exception:
        return ""


def value(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=2500) or ""
    except Exception:
        return ""


def attribute(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=2500) or ""
    except Exception:
        return ""


def parse_counter(label):
    m = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_year(folder_title):
    m = re.search(r"Ano\s+(\d{4})", folder_title or "", re.I)
    return int(m.group(1)) if m else None


def parse_edition(folder_title):
    m = re.search(r"Edi(?:ç|c)ão\s+([^\\/\s]+)", folder_title or "", re.I)
    return m.group(1) if m else ""


def edition_date(edition):
    m = re.search(r"(\d+)", edition or "")
    if not m:
        return "", ""
    issue = int(m.group(1))
    computed = BASE_DATE + timedelta(days=issue - BASE_ISSUE)
    return computed.isoformat(), computed.strftime("%A")


def close_modal(page):
    for selector in [
        '#RadWindowWrapper_PesqOpniaoRadWindow .rwCloseButton',
        '#RadWindowWrapper_PesqOpniaoRadWindow span',
    ]:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=700):
                loc.click(force=True, timeout=2000)
                return
        except Exception:
            pass


def wait_metadata(page, expected_year, expected_query):
    try:
        page.wait_for_selector("#OcorNroLbl", state="attached", timeout=60000)
    except Exception:
        pass
    deadline = time.time() + 25
    last = {}
    while time.time() < deadline:
        label = safe_text(page, "#OcorNroLbl")
        search_value = value(page, "#PesquisarTxt")
        folder_title = attribute(page, "#PastaTxt", "title")
        last = {"label": label, "search_value": search_value, "folder_title": folder_title}
        _, total = parse_counter(label)
        if total is not None and search_value.strip() == expected_query.strip():
            year = parse_year(folder_title)
            if year == expected_year or total == 0:
                return last
        time.sleep(0.4)
    return last


def next_occurrence(page, old_label, old_pagfis):
    try:
        page.locator("#OcorPosBtn").click(force=True, timeout=12000)
    except Exception:
        try:
            page.evaluate("document.querySelector('#OcorPosBtn').click()")
        except Exception:
            return False
    try:
        page.wait_for_function(
            "s => { const l=document.querySelector('#OcorNroLbl'); const p=document.querySelector('#hPagFis'); return (l && (l.textContent||'').trim() !== s.label) || (p && p.value !== s.pagfis); }",
            arg={"label": old_label, "pagfis": old_pagfis},
            timeout=45000,
        )
        return True
    except Exception:
        page.wait_for_timeout(1800)
        return safe_text(page, "#OcorNroLbl") != old_label or value(page, "#hPagFis") != old_pagfis


def ensure_image(page, zoom_clicks=2):
    errors = []
    src = attribute(page, "#DocumentoImg", "src")
    if not src:
        try:
            page.evaluate("() => { const p=document.getElementById('hPagFis'); if (typeof PagCarrega === 'function' && p) PagCarrega(p.value); else { const b=document.getElementById('CarregaImagemHiddenButton'); if (b) b.click(); } }")
            page.wait_for_function(
                "() => { const i=document.querySelector('#DocumentoImg'); return i && i.getAttribute('src'); }",
                timeout=45000,
            )
        except Exception as exc:
            errors.append(f"initial image load: {type(exc).__name__}: {exc}")
    src = attribute(page, "#DocumentoImg", "src")

    for _ in range(zoom_clicks):
        if not src:
            break
        old_src = src
        try:
            page.locator("#ZoomInBtn").click(force=True, timeout=10000)
            page.wait_for_function(
                "old => { const i=document.querySelector('#DocumentoImg'); return i && i.getAttribute('src') && i.getAttribute('src') !== old; }",
                arg=old_src,
                timeout=35000,
            )
            src = attribute(page, "#DocumentoImg", "src")
        except Exception as exc:
            errors.append(f"zoom: {type(exc).__name__}: {exc}")
            src = attribute(page, "#DocumentoImg", "src")
            break
    return src, errors


def save_page_image(page, context, stem):
    result = {"image_src": "", "download": "", "screenshot": "", "image_errors": []}
    src, errors = ensure_image(page, zoom_clicks=2)
    result["image_src"] = src
    result["image_errors"].extend(errors)

    if src:
        try:
            full_url = urljoin(page.url, src)
            response = context.request.get(full_url, timeout=60000, headers={"Referer": page.url})
            if response.ok:
                ctype = response.headers.get("content-type", "")
                ext = ".jpg" if "jpeg" in ctype else ".png" if "png" in ctype else ".bin"
                target = IMG / f"{stem}{ext}"
                target.write_bytes(response.body())
                result["download"] = str(target)
            else:
                result["image_errors"].append(f"download HTTP {response.status}")
        except Exception as exc:
            result["image_errors"].append(f"download: {type(exc).__name__}: {exc}")

    try:
        locator = page.locator("#DocumentoImg")
        if locator.is_visible(timeout=3000):
            shot = IMG / f"{stem}_element.png"
            locator.screenshot(path=str(shot), timeout=45000)
            result["screenshot"] = str(shot)
    except Exception as exc:
        result["image_errors"].append(f"screenshot: {type(exc).__name__}: {exc}")
    return result


def write_csv(rows):
    target = OUT / "candidates.csv"
    fields = [
        "bib", "year", "edition", "computed_date", "weekday", "is_sunday",
        "page", "pagfis", "search_name", "search_query", "tier", "counter_label",
        "duplicate", "docreader_link", "download", "screenshot", "image_src",
    ]
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    summary = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method_note": "Hyphen search is excluded because HDB expands it to 14,403 matches in 028274_03; joined/space and OCR variants were used instead.",
        "collections": COLLECTIONS,
        "searches": SEARCHES,
        "runs": [],
        "candidates": [],
        "errors": [],
    }
    seen_pages = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1920, "height": 1400},
            ignore_https_errors=True,
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        page.set_default_timeout(25000)

        for collection in COLLECTIONS:
            bib = collection["bib"]
            for year in collection["years"]:
                for search in SEARCHES:
                    query = search["query"]
                    url = f"{BASE}?bib={bib}&pasta={quote('ano ' + str(year), safe='')}&Pesq={quote(query, safe='')}"
                    run = {
                        "bib": bib, "year": year, "search": search, "url": url,
                        "status": "started", "counter": "", "total": 0,
                        "visited": 0, "candidate_rows": 0, "error": "",
                    }
                    summary["runs"].append(run)
                    print(f"\n=== {bib} | {year} | {search['name']} | {query} ===", flush=True)
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=90000)
                        close_modal(page)
                        state = wait_metadata(page, year, query)
                        label = state.get("label", "")
                        current, total = parse_counter(label)
                        run["counter"] = label
                        run["total"] = total or 0
                        run["search_value"] = state.get("search_value", "")
                        run["folder_title"] = state.get("folder_title", "")
                        print(json.dumps({"counter": label, **state}, ensure_ascii=False), flush=True)

                        if total is None or total == 0:
                            run["status"] = "no-results"
                            continue
                        if total > 500:
                            run["status"] = "skipped-too-many"
                            run["error"] = f"Safety cap: {total} matches"
                            continue

                        for _ in range(total):
                            label_now = safe_text(page, "#OcorNroLbl")
                            occ_no, occ_total = parse_counter(label_now)
                            folder_title = attribute(page, "#PastaTxt", "title")
                            result_year = parse_year(folder_title)
                            edition = parse_edition(folder_title)
                            pagfis = value(page, "#hPagFis")
                            page_number = value(page, "#PagAtualTxt")
                            acervo = safe_text(page, "#AcervoDescLbl")
                            computed_date, weekday = edition_date(edition)
                            is_sunday = weekday == "Sunday"
                            run["visited"] += 1

                            row = {
                                "bib": bib,
                                "collection": collection["label"],
                                "year": result_year,
                                "edition": edition,
                                "computed_date": computed_date,
                                "weekday": weekday,
                                "is_sunday": is_sunday,
                                "page": page_number,
                                "pagfis": pagfis,
                                "acervo": acervo,
                                "search_name": search["name"],
                                "search_query": query,
                                "tier": search["tier"],
                                "occurrence": occ_no,
                                "occurrence_total": occ_total,
                                "counter_label": label_now,
                                "folder_title": folder_title,
                                "docreader_link": f"https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}" if pagfis else page.url,
                                "source_url": page.url,
                                "duplicate": False,
                                "download": "",
                                "screenshot": "",
                                "image_src": attribute(page, "#DocumentoImg", "src"),
                                "image_errors": [],
                            }

                            if result_year == year:
                                key = (bib, pagfis)
                                row["duplicate"] = key in seen_pages
                                if key not in seen_pages:
                                    seen_pages.add(key)
                                    if is_sunday:
                                        stem = f"{bib}_{year}_{edition}_{pagfis}_p{page_number}_{search['name']}"
                                        row.update(save_page_image(page, context, stem))
                                summary["candidates"].append(row)
                                run["candidate_rows"] += 1
                                print(json.dumps({k: row.get(k) for k in ["counter_label", "edition", "computed_date", "weekday", "page", "pagfis", "duplicate", "download"]}, ensure_ascii=False), flush=True)

                            if occ_no is not None and occ_no >= total:
                                break
                            if not next_occurrence(page, label_now, pagfis):
                                run["error"] = f"Could not advance after {label_now}, pagfis {pagfis}"
                                break
                        run["status"] = "completed" if not run["error"] else "partial"
                    except Exception as exc:
                        run["status"] = "error"
                        run["error"] = f"{type(exc).__name__}: {exc}"
                        summary["errors"].append({
                            "bib": bib, "year": year, "search": search,
                            "error": run["error"], "traceback": traceback.format_exc(),
                        })
                        print(traceback.format_exc(), flush=True)
                    finally:
                        (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                        write_csv(summary["candidates"])
        browser.close()

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["unique_candidate_pages"] = len(seen_pages)
    summary["unique_sunday_pages"] = len({(r["bib"], r["pagfis"]) for r in summary["candidates"] if r.get("is_sunday")})
    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(summary["candidates"])
    print(json.dumps({"unique_candidate_pages": summary["unique_candidate_pages"], "unique_sunday_pages": summary["unique_sunday_pages"]}), flush=True)


if __name__ == "__main__":
    main()
