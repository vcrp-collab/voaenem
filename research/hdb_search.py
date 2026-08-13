import json
import os
import re
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://memoria.bn.gov.br/DocReader/DocReader.aspx"
OUT = Path("hdb_results")
IMG = OUT / "images"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

COLLECTIONS = [
    {"bib": "028274_03", "label": "Correio Braziliense (DF) - 1980 a 1989", "years": {1988, 1989}},
    {"bib": "028274_04", "label": "Correio Braziliense (DF) - 1990 a 1998", "years": {1990, 1991}},
]

# Exact renderings plus common OCR confusions (1/l/I and 5/S).
SEARCHES = [
    {"name": "hyphen", "query": '"248-1165"'},
    {"name": "space", "query": '"248 1165"'},
    {"name": "joined", "query": "2481165"},
    {"name": "dot", "query": '"248.1165"'},
    {"name": "spaced-hyphen", "query": '"248 - 1165"'},
    {"name": "slash", "query": '"248/1165"'},
    {"name": "l165", "query": '"248-l165"'},
    {"name": "1l65", "query": '"248-1l65"'},
    {"name": "ll65", "query": '"248-ll65"'},
    {"name": "I165", "query": '"248-I165"'},
    {"name": "116S", "query": '"248-116S"'},
    {"name": "1l6S", "query": '"248-1l6S"'},
    {"name": "space-1l65", "query": '"248 1l65"'},
    {"name": "space-ll65", "query": '"248 ll65"'},
]


def safe_text(locator):
    try:
        return (locator.text_content(timeout=3000) or "").strip()
    except Exception:
        return ""


def value(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=3000) or ""
    except Exception:
        return ""


def attribute(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=3000) or ""
    except Exception:
        return ""


def parse_year(folder_title):
    m = re.search(r"Ano\s+(\d{4})", folder_title or "", flags=re.I)
    return int(m.group(1)) if m else None


def parse_edition(folder_title):
    m = re.search(r"Edi(?:ç|c)ão\s+([^\\/\s]+)", folder_title or "", flags=re.I)
    return m.group(1) if m else ""


def parse_counter(label):
    m = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def close_modal(page):
    selectors = [
        '#RadWindowWrapper_PesqOpniaoRadWindow .rwCloseButton',
        '#RadWindowWrapper_PesqOpniaoRadWindow span',
        'text=Fechar',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                loc.click(force=True, timeout=3000)
                return True
        except Exception:
            pass
    return False


def wait_search_ready(page):
    try:
        page.wait_for_selector("#DocumentoImg", state="attached", timeout=70000)
    except Exception:
        pass
    deadline = time.time() + 35
    last = ""
    while time.time() < deadline:
        label = safe_text(page.locator("#OcorNroLbl"))
        if label:
            last = label
            if re.search(r"\d+\s*/\s*\d+", label):
                return label
        time.sleep(0.5)
    return last


def zoom(page, clicks=2):
    done = 0
    for _ in range(clicks):
        try:
            btn = page.locator("#ZoomInBtn")
            if not btn.count():
                break
            old_src = attribute(page, "#DocumentoImg", "src")
            btn.click(force=True, timeout=8000)
            try:
                page.wait_for_function(
                    "old => { const i=document.querySelector('#DocumentoImg'); return i && i.src && i.src !== old; }",
                    arg=old_src,
                    timeout=25000,
                )
            except Exception:
                page.wait_for_timeout(2500)
            done += 1
        except Exception:
            break
    return done


def save_occurrence_image(page, context, stem):
    results = {"screenshot": "", "download": "", "image_src": "", "errors": []}
    try:
        img = page.locator("#DocumentoImg")
        img.wait_for(state="visible", timeout=30000)
        src = img.get_attribute("src") or ""
        results["image_src"] = src
        shot = IMG / f"{stem}.png"
        img.screenshot(path=str(shot), timeout=60000)
        results["screenshot"] = str(shot)
    except Exception as exc:
        results["errors"].append(f"screenshot: {exc}")

    try:
        src = results["image_src"]
        if src:
            full = urljoin(page.url, src)
            response = context.request.get(full, timeout=60000, headers={"Referer": page.url})
            if response.ok:
                ctype = response.headers.get("content-type", "")
                ext = ".jpg" if "jpeg" in ctype else ".png" if "png" in ctype else ".bin"
                target = IMG / f"{stem}_source{ext}"
                target.write_bytes(response.body())
                results["download"] = str(target)
            else:
                results["errors"].append(f"download status {response.status}")
    except Exception as exc:
        results["errors"].append(f"download: {exc}")
    return results


def next_occurrence(page, old_label, old_src):
    try:
        btn = page.locator("#OcorPosBtn")
        btn.click(force=True, timeout=15000)
    except Exception:
        try:
            page.evaluate("document.querySelector('#OcorPosBtn').click()")
        except Exception:
            return False
    try:
        page.wait_for_function(
            "state => { const l=document.querySelector('#OcorNroLbl'); const i=document.querySelector('#DocumentoImg'); const lt=l ? (l.textContent||'').trim() : ''; const src=i ? (i.getAttribute('src')||'') : ''; return (lt && lt !== state.label) || (src && src !== state.src); }",
            arg={"label": old_label, "src": old_src},
            timeout=60000,
        )
        return True
    except Exception:
        page.wait_for_timeout(3000)
        new_label = safe_text(page.locator("#OcorNroLbl"))
        new_src = attribute(page, "#DocumentoImg", "src")
        return new_label != old_label or new_src != old_src


def main():
    summary = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "collections": COLLECTIONS,
        "searches": SEARCHES,
        "runs": [],
        "occurrences": [],
        "errors": [],
    }
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--window-size=1920,1400",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1920, "height": 1400},
            ignore_https_errors=True,
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        page.set_default_timeout(30000)

        for collection in COLLECTIONS:
            bib = collection["bib"]
            years = collection["years"]
            for search in SEARCHES:
                query = search["query"]
                url = f"{BASE}?bib={bib}&Pesq={quote(query, safe='')}"
                run = {"bib": bib, "search": search, "url": url, "status": "started", "label": "", "total": 0, "visited": 0, "saved": 0, "error": ""}
                summary["runs"].append(run)
                print(f"\n=== {bib} | {search['name']} | {query} ===", flush=True)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    close_modal(page)
                    label = wait_search_ready(page)
                    run["label"] = label
                    cur, total = parse_counter(label)
                    if not total:
                        run["status"] = "no-occurrences"
                        run["body_text"] = (page.locator("body").inner_text(timeout=5000) or "")[:3000]
                        print(f"No occurrence counter. Label={label!r}", flush=True)
                        continue
                    run["total"] = total
                    print(f"Counter: {cur}/{total}", flush=True)
                    if total > 400:
                        run["status"] = "skipped-too-many"
                        run["error"] = f"Search returned {total} occurrences; safety cap is 400"
                        continue

                    zoomed = zoom(page, clicks=2)
                    run["zoom_clicks"] = zoomed

                    for _ in range(total):
                        label_now = safe_text(page.locator("#OcorNroLbl"))
                        occ_no, occ_total = parse_counter(label_now)
                        folder_title = attribute(page, "#PastaTxt", "title")
                        folder_value = value(page, "#PastaTxt")
                        year = parse_year(folder_title)
                        edition = parse_edition(folder_title)
                        pagfis = value(page, "#hPagFis")
                        page_number = value(page, "#PagAtualTxt")
                        acervo = safe_text(page.locator("#AcervoDescLbl"))
                        img_src = attribute(page, "#DocumentoImg", "src")
                        run["visited"] += 1

                        occurrence = {
                            "bib": bib,
                            "collection": collection["label"],
                            "search_name": search["name"],
                            "search_query": query,
                            "occurrence": occ_no,
                            "occurrence_total": occ_total,
                            "counter_label": label_now,
                            "year": year,
                            "edition": edition,
                            "folder_title": folder_title,
                            "folder_value": folder_value,
                            "page": page_number,
                            "pagfis": pagfis,
                            "acervo": acervo,
                            "image_src": img_src,
                            "docreader_link": f"https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}" if pagfis else page.url,
                            "source_url": page.url,
                        }

                        if year in years:
                            key = (bib, pagfis or img_src)
                            occurrence["duplicate"] = key in seen
                            if key not in seen:
                                seen.add(key)
                                stem = f"{bib}_{year}_{edition or 'ed'}_{pagfis or occ_no}_{search['name']}"
                                files = save_occurrence_image(page, context, stem)
                                occurrence.update(files)
                                run["saved"] += 1
                            summary["occurrences"].append(occurrence)
                            print(json.dumps({k: occurrence.get(k) for k in ["search_name","counter_label","year","edition","page","pagfis","docreader_link","duplicate"]}, ensure_ascii=False), flush=True)

                        if occ_no is not None and occ_no >= total:
                            break
                        old_label = label_now
                        old_src = img_src
                        if not next_occurrence(page, old_label, old_src):
                            run["error"] = f"Could not advance after {old_label}"
                            break
                    run["status"] = "completed" if not run["error"] else "partial"
                except Exception as exc:
                    run["status"] = "error"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                    summary["errors"].append({"bib": bib, "search": search, "error": run["error"], "traceback": traceback.format_exc()})
                    print(traceback.format_exc(), flush=True)
                finally:
                    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        browser.close()

    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["unique_candidate_pages"] = len(seen)
    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFinished. Unique candidate pages: {len(seen)}", flush=True)


if __name__ == "__main__":
    main()
