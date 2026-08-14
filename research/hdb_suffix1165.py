import json
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)

YEAR_COLLECTIONS = [
    (1988, "028274_03"),
    (1989, "028274_03"),
    (1990, "028274_04"),
    (1991, "028274_04"),
]

QUERIES = [
    "1165",
    '"1165"',
    "1165 classificados",
    '"1165" classificados',
    "-1165 classificados",
    '"-1165" classificados',
    "I165 classificados",
    "l165 classificados",
    "116S classificados",
    "11G5 classificados",
    "1I65 classificados",
    "1l65 classificados",
    "ll65 classificados",
    "I16S classificados",
    "l16S classificados",
]


def get_text(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=3000) or "").strip()
    except Exception:
        return ""


def get_value(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=3000) or ""
    except Exception:
        return ""


def get_attribute(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=3000) or ""
    except Exception:
        return ""


def parse_counter(label):
    match = re.search(r"(\d+)\s*/\s*(\d+)", label or "")
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def wait_ready(page, query, timeout=35):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        label = get_text(page, "#OcorNroLbl")
        observed = get_value(page, "#PesquisarTxt")
        folder = get_attribute(page, "#PastaTxt", "title") or get_text(page, "#PastaTxt")
        current, total = parse_counter(label)
        last = {
            "label": label,
            "observed": observed,
            "folder": folder,
            "current": current,
            "total": total,
            "pagfis": get_value(page, "#hPagFis"),
            "logical_page": get_value(page, "#PagAtualTxt"),
        }
        if total is not None and observed.strip() == query:
            return last
        time.sleep(0.35)
    return last


def main():
    report = {"runs": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1600, "height": 1200},
            ignore_https_errors=True,
        )
        for year, bib in YEAR_COLLECTIONS:
            for query in QUERIES:
                page = context.new_page()
                item = {"year_requested": year, "bib": bib, "query": query, "status": "started", "error": ""}
                report["runs"].append(item)
                try:
                    url = (
                        "https://memoria.bn.gov.br/DocReader/DocReader.aspx"
                        f"?bib={bib}&pasta={quote('ano ' + str(year), safe='')}&Pesq={quote(query, safe='')}"
                    )
                    item["url"] = url
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#PesquisarTxt", state="attached", timeout=70000)
                    state = wait_ready(page, query)
                    item.update(state)
                    item["status"] = "ok" if state.get("total") is not None else "unresolved"
                except Exception as exc:
                    item["status"] = "error"
                    item["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    page.close()
                    (OUT / "suffix1165_query_counts.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    print(json.dumps(item, ensure_ascii=False), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
