import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)
COLLECTIONS = ["028274_03", "028274_04"]
QUERIES = ["1165", "1165 classificados", "11G5 classificados"]
ANCHOR_ISSUE = 9250
ANCHOR_DATE = date(1988, 8, 14)


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


def current_state(page):
    label = text(page, "#OcorNroLbl")
    match = re.search(r"(\d+)\s*/\s*(\d+)", label)
    folder = attr(page, "#PastaTxt", "title") or text(page, "#PastaTxt")
    folder_match = re.search(r"Ano\s+(\d{4})\\Edi(?:ç|c)ão\s+([0-9A-Z]+)", folder, re.I)
    return {
        "counter": label,
        "current": int(match.group(1)) if match else None,
        "total": int(match.group(2)) if match else None,
        "folder": folder,
        "folder_year": int(folder_match.group(1)) if folder_match else None,
        "issue": folder_match.group(2) if folder_match else "",
        "page": value(page, "#PagAtualTxt"),
        "pagfis": value(page, "#hPagFis"),
        "observed": value(page, "#PesquisarTxt"),
    }


def calculated_date(issue):
    match = re.match(r"\d+", issue or "")
    if not match:
        return None
    return ANCHOR_DATE + timedelta(days=int(match.group()) - ANCHOR_ISSUE)


def wait_ready(page, query):
    deadline = time.time() + 45
    state = {}
    while time.time() < deadline:
        state = current_state(page)
        if state.get("total") is not None and state.get("observed", "").strip() == query:
            return state
        time.sleep(0.35)
    return state


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
            page.wait_for_timeout(1000 + attempt * 500)
            state = current_state(page)
            if state.get("counter") != old_counter or state.get("pagfis") != old_pagfis:
                return True
    return False


def main():
    report = {"runs": [], "rows": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            locale="pt-BR", viewport={"width": 1600, "height": 1200}, ignore_https_errors=True,
        )
        for bib in COLLECTIONS:
            for query in QUERIES:
                run = {"bib": bib, "query": query, "status": "started", "total": 0, "visited": 0, "error": ""}
                report["runs"].append(run)
                page = context.new_page()
                try:
                    url = f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&Pesq={quote(query, safe='')}"
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#OcorNroLbl", state="attached", timeout=70000)
                    first = wait_ready(page, query)
                    total = first.get("total") or 0
                    run["total"] = total
                    seen = set()
                    for _ in range(total + 2):
                        state = current_state(page)
                        marker = (state.get("counter"), state.get("pagfis"))
                        if marker in seen:
                            break
                        seen.add(marker)
                        run["visited"] += 1
                        day = calculated_date(state.get("issue"))
                        if day and date(1988, 1, 1) <= day <= date(1991, 12, 31):
                            report["rows"].append({
                                "bib": bib, "query": query, "counter": state.get("counter"),
                                "folder": state.get("folder"), "folder_year": state.get("folder_year"),
                                "issue": state.get("issue"), "date": day.isoformat(),
                                "weekday": day.strftime("%A"), "is_sunday": day.weekday() == 6,
                                "page": state.get("page"), "pagfis": state.get("pagfis"),
                                "link": f"https://memoria.bn.gov.br/DocReader/{bib}/{state.get('pagfis')}",
                            })
                        if state.get("current") is not None and state.get("current") >= total:
                            break
                        if not advance(page, state.get("counter"), state.get("pagfis")):
                            run["error"] = f"failed after {state.get('counter')}"
                            break
                    run["status"] = "completed" if not run["error"] else "partial"
                except Exception as exc:
                    run["status"] = "error"
                    run["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    page.close()
                    (OUT / "suffix1165_enumeration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(run, ensure_ascii=False), flush=True)
        browser.close()
    sundays = [row for row in report["rows"] if row["is_sunday"]]
    report["sundays"] = sundays
    report["summary"] = {"rows": len(report["rows"]), "sunday_rows": len(sundays)}
    (OUT / "suffix1165_enumeration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
