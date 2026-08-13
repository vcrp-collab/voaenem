import json
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)

TESTS = [
    ("https-joined", "https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=2481165"),
    ("https-hyphen", "https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=248-1165"),
    ("https-space", "https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=248%201165"),
    ("https-quoted-joined", "https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=%222481165%22"),
    ("https-quoted-hyphen", "https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=%22248-1165%22"),
    ("http-joined", "http://memoria.bn.gov.br/DocReader/DocReader.aspx?bib=028274_03&Pesq=2481165"),
]


def txt(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=2000) or "").strip()
    except Exception:
        return ""


def val(page, selector):
    try:
        return page.locator(selector).get_attribute("value", timeout=2000) or ""
    except Exception:
        return ""


def attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=2000) or ""
    except Exception:
        return ""


def snapshot(page, elapsed):
    try:
        loading = page.locator("#updateprogressloaddiv").is_visible(timeout=500)
    except Exception:
        loading = False
    return {
        "elapsed": elapsed,
        "url": page.url,
        "title": page.title(),
        "counter": txt(page, "#OcorNroLbl"),
        "search_value": val(page, "#PesquisarTxt"),
        "folder_title": attr(page, "#PastaTxt", "title"),
        "pagfis": val(page, "#hPagFis"),
        "page": val(page, "#PagAtualTxt"),
        "loading": loading,
    }


def main():
    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1600, "height": 1200},
            ignore_https_errors=True,
        )
        page = context.new_page()
        for name, url in TESTS:
            item = {"name": name, "requested_url": url, "states": [], "matching_controls": [], "error": ""}
            print(f"\n=== {name} ===", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                for target in [0, 2, 5, 10, 20]:
                    if target:
                        previous = item["states"][-1]["elapsed"]
                        page.wait_for_timeout((target - previous) * 1000)
                    state = snapshot(page, target)
                    item["states"].append(state)
                    print(json.dumps(state, ensure_ascii=False), flush=True)
                item["matching_controls"] = page.locator("input,button,a,select").evaluate_all("els => els.map(e => ({tag:e.tagName,id:e.id,name:e.name,type:e.type,value:e.value,title:e.title,aria:e.getAttribute('aria-label')})).filter(x => /pesq|search|procur/i.test([x.id,x.name,x.value,x.title,x.aria].join(' '))).slice(0,100)")
                print(json.dumps(item["matching_controls"], ensure_ascii=False), flush=True)
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
                (OUT / f"{name}.html").write_text(page.content(), encoding="utf-8")
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
                print(item["error"], flush=True)
            report.append(item)
            (OUT / "diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()


if __name__ == "__main__":
    main()
