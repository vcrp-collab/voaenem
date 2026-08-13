import json
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
IMG = OUT / "images"
HTML = OUT / "html"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)
HTML.mkdir(exist_ok=True)

TARGETS = [
    {"bib": "028274_03", "pagfis": "116342", "date": "1988-07-31", "issue": "09236", "page": "44"},
    {"bib": "028274_03", "pagfis": "121730", "date": "1988-12-04", "issue": "09362", "page": "47"},
    {"bib": "028274_03", "pagfis": "122022", "date": "1988-12-11", "issue": "09369", "page": "53"},
    {"bib": "028274_03", "pagfis": "125219", "date": "1989-03-05", "issue": "09453", "page": "29"},
]


def attr(page, selector, name):
    try:
        return page.locator(selector).get_attribute(name, timeout=4000) or ""
    except Exception:
        return ""


def value(page, selector):
    return attr(page, selector, "value")


def text(page, selector):
    try:
        return (page.locator(selector).text_content(timeout=4000) or "").strip()
    except Exception:
        return ""


def close_modal(page):
    for selector in [
        '#RadWindowWrapper_PesqOpniaoRadWindow .rwCloseButton',
        '#RadWindowWrapper_PesqOpniaoRadWindow span',
    ]:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=800):
                loc.click(force=True, timeout=3000)
                return
        except Exception:
            pass


def save_response_body(response, target):
    try:
        body = response.body()
        target.write_bytes(body)
        return str(target)
    except Exception:
        return ""


def main():
    report = {"started": time.time(), "targets": [], "errors": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1920, "height": 1400},
            screen={"width": 1920, "height": 1400},
            ignore_https_errors=True,
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        for target in TARGETS:
            bib = target["bib"]
            pagfis = target["pagfis"]
            stem = f"{bib}_{target['date']}_{target['issue']}_{pagfis}_p{target['page']}"
            item = dict(target)
            item.update({
                "url": f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={bib}&pagfis={pagfis}",
                "folder": "", "page_read": "", "hidden_id": "", "hidden_size": "",
                "image_src": "", "image_file": "", "element_screenshot": "", "full_screenshot": "",
                "export_url": "", "export_html": "", "export_screenshot": "",
                "network": [], "console": [], "page_errors": [], "error": "",
            })
            report["targets"].append(item)
            page = context.new_page()
            page.set_default_timeout(30000)

            def on_response(response):
                url = response.url
                lower = url.lower()
                if any(token in lower for token in ["documento", "imagem", "image", ".jpg", ".jpeg", ".png", "pdfexport", "carregaimagem"]):
                    item["network"].append({
                        "url": url,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    })

            page.on("response", on_response)
            page.on("console", lambda msg: item["console"].append(f"{msg.type}: {msg.text}"))
            page.on("pageerror", lambda exc: item["page_errors"].append(str(exc)))

            try:
                print(f"\n=== {item['url']} ===", flush=True)
                page.goto(item["url"], wait_until="domcontentloaded", timeout=90000)
                close_modal(page)
                page.wait_for_selector("#hPagFis", state="attached", timeout=60000)
                item["folder"] = attr(page, "#PastaTxt", "title")
                item["page_read"] = value(page, "#PagAtualTxt")
                item["hidden_id"] = value(page, "#HiddenID")

                page.evaluate(
                    "pf => {"
                    "  const hs=document.getElementById('HiddenSize');"
                    "  if (hs) hs.value='1400x1150';"
                    "  if (typeof ajustadiv==='function') { try { ajustadiv(1400,1150); } catch(e) {} }"
                    "  const hp=document.getElementById('hPagFis'); if (hp) hp.value=pf;"
                    "  if (typeof PagCarrega==='function') PagCarrega(pf);"
                    "  else { const b=document.getElementById('CarregaImagemHiddenButton'); if (b) b.click(); }"
                    "}",
                    pagfis,
                )
                item["hidden_size"] = value(page, "#HiddenSize")

                try:
                    page.wait_for_function(
                        "() => { const i=document.querySelector('#DocumentoImg'); return !!(i && i.getAttribute('src')); }",
                        timeout=120000,
                    )
                except Exception as exc:
                    item["page_errors"].append(f"image wait: {type(exc).__name__}: {exc}")

                src = attr(page, "#DocumentoImg", "src")
                item["image_src"] = src
                if src:
                    try:
                        response = context.request.get(urljoin(page.url, src), timeout=90000, headers={"Referer": page.url})
                        ctype = response.headers.get("content-type", "")
                        ext = ".jpg" if "jpeg" in ctype else ".png" if "png" in ctype else ".bin"
                        image_path = IMG / f"{stem}{ext}"
                        image_path.write_bytes(response.body())
                        item["image_file"] = str(image_path)
                    except Exception as exc:
                        item["page_errors"].append(f"image download: {type(exc).__name__}: {exc}")

                    try:
                        elshot = IMG / f"{stem}_element.png"
                        page.locator("#DocumentoImg").screenshot(path=str(elshot), timeout=90000)
                        item["element_screenshot"] = str(elshot)
                    except Exception as exc:
                        item["page_errors"].append(f"element screenshot: {type(exc).__name__}: {exc}")

                fullshot = IMG / f"{stem}_full.png"
                page.screenshot(path=str(fullshot), full_page=True, timeout=90000)
                item["full_screenshot"] = str(fullshot)
                page_html = HTML / f"{stem}_docreader.html"
                page_html.write_text(page.content(), encoding="utf-8")

                hidden_id = item["hidden_id"]
                if hidden_id:
                    export_url = f"https://memoria.bn.gov.br/DocReader/PDFExportAnx.aspx?id={hidden_id}&pagfis={pagfis}&bib={bib}"
                    item["export_url"] = export_url
                    export_page = context.new_page()
                    try:
                        export_page.goto(export_url, wait_until="domcontentloaded", timeout=90000)
                        export_page.wait_for_timeout(3000)
                        export_html_path = HTML / f"{stem}_export.html"
                        export_html_path.write_text(export_page.content(), encoding="utf-8")
                        item["export_html"] = str(export_html_path)
                        export_shot = IMG / f"{stem}_export.png"
                        export_page.screenshot(path=str(export_shot), full_page=True, timeout=90000)
                        item["export_screenshot"] = str(export_shot)
                        item["export_links"] = export_page.locator("a").evaluate_all(
                            "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href})).filter(x => x.href)"
                        )
                        item["export_inputs"] = export_page.locator("input,button,select").evaluate_all(
                            "els => els.map(e => ({tag:e.tagName,id:e.id,name:e.name,type:e.type,value:e.value,title:e.title})).slice(0,150)"
                        )
                    except Exception as exc:
                        item["page_errors"].append(f"export page: {type(exc).__name__}: {exc}")
                    finally:
                        export_page.close()

                print(json.dumps({k: item.get(k) for k in ["date","folder","page_read","hidden_id","hidden_size","image_src","image_file","export_url","page_errors"]}, ensure_ascii=False), flush=True)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
                report["errors"].append({"target": target, "error": item["error"], "traceback": traceback.format_exc()})
                print(traceback.format_exc(), flush=True)
            finally:
                page.close()
                (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        browser.close()

    report["finished"] = time.time()
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
