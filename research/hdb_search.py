import json
import re
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)

COLLECTIONS = [
    {"bib": "028274_03", "years": {1988, 1989}},
    {"bib": "028274_04", "years": {1990, 1991}},
]

QUERIES = [
    {"name": "joined", "value": "2481165", "confidence": "exact"},
    {"name": "quoted-space", "value": '"248 1165"', "confidence": "exact-tokenized"},
    {"name": "joined-l165", "value": "248l165", "confidence": "ocr"},
    {"name": "joined-1l65", "value": "2481l65", "confidence": "ocr"},
    {"name": "joined-ll65", "value": "248ll65", "confidence": "ocr"},
    {"name": "joined-I165", "value": "248I165", "confidence": "ocr"},
    {"name": "joined-116S", "value": "248116S", "confidence": "ocr"},
    {"name": "joined-I16S", "value": "248I16S", "confidence": "ocr"},
    {"name": "joined-ll6S", "value": "248ll6S", "confidence": "ocr"},
    {"name": "joined-Z48", "value": "Z481165", "confidence": "ocr"},
    {"name": "joined-24B", "value": "24B1165", "confidence": "ocr"},
    {"name": "quoted-l165", "value": '"248 l165"', "confidence": "ocr-tokenized"},
    {"name": "quoted-1l65", "value": '"248 1l65"', "confidence": "ocr-tokenized"},
    {"name": "quoted-I165", "value": '"248 I165"', "confidence": "ocr-tokenized"},
    {"name": "quoted-116S", "value": '"248 116S"', "confidence": "ocr-tokenized"},
]

BASE_ISSUE = 9250
BASE_DATE = date(1988, 8, 14)


def txt(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=2500) or "").strip()
    except Exception:
        return ""


def val(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=2500) or ""
    except Exception:
        return ""


def attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=2500) or ""
    except Exception:
        return ""


def parse_counter(label):
    m = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_folder(folder):
    ym = re.search(r"Ano\s+(\d{4})", folder or "", re.I)
    em = re.search(r"Edi(?:ç|c)ão\s+(\d+)", folder or "", re.I)
    year = int(ym.group(1)) if ym else None
    issue = em.group(1) if em else ""
    if issue:
        d = BASE_DATE + timedelta(days=int(issue) - BASE_ISSUE)
        ds, weekday = d.isoformat(), d.strftime("%A")
    else:
        ds = weekday = ""
    return year, issue, ds, weekday


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


def next_occurrence(page, old_label, old_pagfis):
    try:
        page.locator("#OcorPosBtn").click(force=True, timeout=10000)
    except Exception:
        try:
            page.evaluate("document.querySelector('#OcorPosBtn').click()")
        except Exception:
            return False
    try:
        page.wait_for_function(
            "s => { const l=document.querySelector('#OcorNroLbl'); const p=document.querySelector('#hPagFis'); return (l && (l.textContent||'').trim() !== s.label) || (p && p.value !== s.pagfis); }",
            arg={"label": old_label, "pagfis": old_pagfis},
            timeout=35000,
        )
        return True
    except Exception:
        page.wait_for_timeout(1000)
        return txt(page, "#OcorNroLbl") != old_label or val(page, "#hPagFis") != old_pagfis


def wait_query(page, expected):
    deadline = time.time() + 18
    state = {}
    while time.time() < deadline:
        state = {
            "counter": txt(page, "#OcorNroLbl"),
            "query": val(page, "#PesquisarTxt"),
            "folder": attr(page, "#PastaTxt", "title"),
        }
        _, total = parse_counter(state["counter"])
        if total is not None and state["query"].strip() == expected.strip():
            return state
        time.sleep(0.35)
    return state


def main():
    result = {
        "started": time.time(),
        "queries": QUERIES,
        "runs": [],
        "rows": [],
        "errors": [],
    }
    seen_variant_occurrences = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1600, "height": 1200},
            ignore_https_errors=True,
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        for collection in COLLECTIONS:
            bib = collection["bib"]
            years = collection["years"]
            for query in QUERIES:
                page = context.new_page()
                page.set_default_timeout(25000)
                encoded = quote(query["value"], safe="")
                url = f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={encoded}"
                run = {
                    "bib": bib, "query": query, "url": url, "status": "started",
                    "counter": "", "total": 0, "visited": 0, "matched": 0, "error": "",
                }
                result["runs"].append(run)
                print(f"\n=== {bib} | {query['name']} | {query['value']} ===", flush=True)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    close_modal(page)
                    state = wait_query(page, query["value"])
                    run["counter"] = state.get("counter", "")
                    run["observed_query"] = state.get("query", "")
                    current, total = parse_counter(run["counter"])
                    run["total"] = total or 0
                    print(json.dumps(state, ensure_ascii=False), flush=True)

                    if total is None or total == 0:
                        run["status"] = "no-results"
                        continue
                    if total > 350:
                        run["status"] = "skipped-too-many"
                        run["error"] = f"{total} results exceeds safety cap"
                        continue

                    for _ in range(total):
                        label = txt(page, "#OcorNroLbl")
                        n, t = parse_counter(label)
                        folder = attr(page, "#PastaTxt", "title")
                        year, issue, ds, weekday = parse_folder(folder)
                        pagfis = val(page, "#hPagFis")
                        pageno = val(page, "#PagAtualTxt")
                        run["visited"] += 1

                        if year in years:
                            key = (bib, query["name"], pagfis, label)
                            if key not in seen_variant_occurrences:
                                seen_variant_occurrences.add(key)
                                row = {
                                    "bib": bib,
                                    "query_name": query["name"],
                                    "query_value": query["value"],
                                    "confidence": query["confidence"],
                                    "counter": label,
                                    "year": year,
                                    "issue": issue,
                                    "date": ds,
                                    "weekday": weekday,
                                    "is_sunday": weekday == "Sunday",
                                    "page": pageno,
                                    "pagfis": pagfis,
                                    "folder": folder,
                                    "link": f"https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}" if pagfis else page.url,
                                }
                                result["rows"].append(row)
                                run["matched"] += 1
                                if row["is_sunday"]:
                                    print("SUNDAY " + json.dumps(row, ensure_ascii=False), flush=True)

                        if n is not None and n >= total:
                            break
                        if not next_occurrence(page, label, pagfis):
                            run["error"] = f"could not advance after {label} / {pagfis}"
                            break
                    run["status"] = "completed" if not run["error"] else "partial"
                except Exception as exc:
                    run["status"] = "error"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                    result["errors"].append({
                        "bib": bib, "query": query, "error": run["error"],
                        "traceback": traceback.format_exc(),
                    })
                    print(traceback.format_exc(), flush=True)
                finally:
                    page.close()
                    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        browser.close()

    result["finished"] = time.time()
    sunday_pages = {}
    for row in result["rows"]:
        if row["is_sunday"]:
            key = f"{row['bib']}:{row['pagfis']}"
            sunday_pages.setdefault(key, {"bib": row["bib"], "pagfis": row["pagfis"], "date": row["date"], "issue": row["issue"], "page": row["page"], "link": row["link"], "variants": []})
            sunday_pages[key]["variants"].append({"name": row["query_name"], "value": row["query_value"], "confidence": row["confidence"]})
    result["sunday_pages"] = list(sunday_pages.values())
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(result["rows"]), "sunday_pages": result["sunday_pages"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
