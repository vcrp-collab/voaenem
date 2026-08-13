import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)

QUERIES = [
    "2481165 classificados",
    '"248 1165" classificados',
]
COLLECTIONS = ["028274_03", "028274_04"]


def text(page, selector):
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
    match = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def next_occurrence(page, old_label, old_page):
    try:
        page.locator("#OcorPosBtn").click(force=True, timeout=12000)
        page.wait_for_function(
            "s => { const l=document.querySelector('#OcorNroLbl'); const p=document.querySelector('#hPagFis'); return (l && (l.textContent||'').trim() !== s.label) || (p && p.value !== s.page); }",
            arg={"label": old_label, "page": old_page},
            timeout=45000,
        )
        return True
    except Exception:
        page.wait_for_timeout(1200)
        return text(page, "#OcorNroLbl") != old_label or value(page, "#hPagFis") != old_page


def main():
    report = {"runs": [], "pages": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1600, "height": 1200},
            ignore_https_errors=True,
        )
        for bib in COLLECTIONS:
            for query in QUERIES:
                page = context.new_page()
                run = {"bib": bib, "query": query, "counter": "", "total": 0, "status": "started", "error": ""}
                report["runs"].append(run)
                try:
                    url = f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe='')}"
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#OcorNroLbl", state="attached", timeout=60000)
                    deadline = time.time() + 20
                    while time.time() < deadline:
                        label = text(page, "#OcorNroLbl")
                        observed = value(page, "#PesquisarTxt")
                        current, total = parse_counter(label)
                        if total is not None and observed.strip() == query:
                            break
                        time.sleep(0.4)
                    run["counter"] = label
                    run["total"] = total or 0
                    if not total:
                        run["status"] = "no-results"
                        continue
                    if total > 300:
                        run["status"] = "too-many"
                        continue
                    for _ in range(total):
                        label = text(page, "#OcorNroLbl")
                        number, _ = parse_counter(label)
                        pagfis = value(page, "#hPagFis")
                        folder = attribute(page, "#PastaTxt", "title")
                        logical_page = value(page, "#PagAtualTxt")
                        report["pages"].append({
                            "bib": bib,
                            "query": query,
                            "counter": label,
                            "pagfis": pagfis,
                            "folder": folder,
                            "page": logical_page,
                            "link": f"https://memoria.bn.gov.br/DocReader/{bib}/{pagfis}",
                        })
                        if number is not None and number >= total:
                            break
                        if not next_occurrence(page, label, pagfis):
                            run["error"] = f"Falha após {label}"
                            break
                    run["status"] = "completed" if not run["error"] else "partial"
                except Exception as exc:
                    run["status"] = "error"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    page.close()
                    (OUT / "classified_crosscheck.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
