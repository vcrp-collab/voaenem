import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)
COLLECTIONS = ["028274_03", "028274_04"]
QUERIES = [
    "1165 domingo",
    "1165 domingo classificados",
    "11G5 domingo",
    "11G5 domingo classificados",
    "116S domingo classificados",
    "I165 domingo classificados",
    "l165 domingo classificados",
    "1I65 domingo classificados",
    "1l65 domingo classificados",
]


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


def attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=2500) or ""
    except Exception:
        return ""


def state(page):
    label = text(page, "#OcorNroLbl")
    match = re.search(r"(\d+)\s*/\s*(\d+)", label)
    folder = attr(page, "#PastaTxt", "title") or text(page, "#PastaTxt")
    folder_match = re.search(r"Ano\s+(\d{4})\\Edi(?:ç|c)ão\s+([0-9A-Z]+)", folder, re.I)
    return {
        "counter": label,
        "current": int(match.group(1)) if match else None,
        "total": int(match.group(2)) if match else None,
        "folder": folder,
        "year": int(folder_match.group(1)) if folder_match else None,
        "issue": folder_match.group(2) if folder_match else "",
        "page": value(page, "#PagAtualTxt"),
        "pagfis": value(page, "#hPagFis"),
        "observed": value(page, "#PesquisarTxt"),
    }


def wait_ready(page, query):
    deadline = time.time() + 40
    current = {}
    while time.time() < deadline:
        current = state(page)
        if current.get("total") is not None and current.get("observed", "").strip() == query:
            return current
        time.sleep(0.35)
    return current


def advance(page, old_counter, old_pagfis):
    for attempt in range(3):
        try:
            page.locator("#OcorPosBtn").click(force=True, timeout=12000)
            page.wait_for_function(
                "s=>{const a=document.querySelector('#OcorNroLbl'),b=document.querySelector('#hPagFis');return(a&&(a.textContent||'').trim()!=s.c)||(b&&(b.value||'')!=s.p)}",
                arg={"c": old_counter, "p": old_pagfis},
                timeout=35000,
            )
            return True
        except Exception:
            page.wait_for_timeout(800 + attempt * 500)
            current = state(page)
            if current.get("counter") != old_counter or current.get("pagfis") != old_pagfis:
                return True
    return False


def main():
    report = {"runs": [], "rows": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            locale="pt-BR", viewport={"width": 1600, "height": 1200}, ignore_https_errors=True,
        )
        for bib in COLLECTIONS:
            for query in QUERIES:
                run = {"bib": bib, "query": query, "total": 0, "visited": 0, "status": "started", "error": ""}
                report["runs"].append(run)
                page = context.new_page()
                try:
                    page.goto(
                        f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe='')}",
                        wait_until="domcontentloaded", timeout=90000,
                    )
                    page.wait_for_selector("#OcorNroLbl", state="attached", timeout=70000)
                    first = wait_ready(page, query)
                    total = first.get("total") or 0
                    run["total"] = total
                    seen = set()
                    for _ in range(total + 2):
                        current = state(page)
                        marker = (current.get("counter"), current.get("pagfis"))
                        if marker in seen:
                            break
                        seen.add(marker)
                        run["visited"] += 1
                        if current.get("year") in {1988, 1989, 1990, 1991}:
                            report["rows"].append({
                                "bib": bib, "query": query, "counter": current.get("counter"),
                                "folder": current.get("folder"), "year": current.get("year"),
                                "issue": current.get("issue"), "page": current.get("page"),
                                "pagfis": current.get("pagfis"),
                                "link": f"https://memoria.bn.gov.br/DocReader/{bib}/{current.get('pagfis')}",
                            })
                        if current.get("current") is not None and current.get("current") >= total:
                            break
                        if not advance(page, current.get("counter"), current.get("pagfis")):
                            run["error"] = f"failed after {current.get('counter')}"
                            break
                    run["status"] = "completed" if not run["error"] else "partial"
                except Exception as exc:
                    run["status"] = "error"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    page.close()
                    (OUT / "suffix1165_sunday_queries.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(run, ensure_ascii=False), flush=True)
        browser.close()
    unique = {}
    for row in report["rows"]:
        key = (row["bib"], row["pagfis"])
        item = unique.setdefault(key, {k: row[k] for k in ["bib", "folder", "year", "issue", "page", "pagfis", "link"]})
        item.setdefault("queries", []).append(row["query"])
    report["unique_pages"] = sorted(unique.values(), key=lambda x: (x["year"], x["issue"], int(x["page"] or 0)))
    report["summary"] = {"rows": len(report["rows"]), "unique_pages": len(report["unique_pages"])}
    (OUT / "suffix1165_sunday_queries.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
