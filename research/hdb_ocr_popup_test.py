import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)
TARGETS = [
    {"bib": "028274_03", "pagfis": "117203", "query": "1165", "label": "page117203"},
    {"bib": "028274_03", "pagfis": "116342", "query": "1165", "label": "page116342"},
]


def clean(value):
    return (value or "").strip()


def main():
    report = {"targets": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/151 Mobile Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1280, "height": 1800},
            is_mobile=True,
            has_touch=True,
            ignore_https_errors=True,
        )
        for target in TARGETS:
            item = dict(target)
            item.update({"url": "", "button": False, "text": "", "body": "", "errors": [], "status": "started"})
            report["targets"].append(item)
            page = context.new_page()
            page.on("pageerror", lambda exc, target_item=item: target_item["errors"].append(str(exc)))
            try:
                url = f"https://memoria.bn.gov.br/DocReader/DocReaderMobile.aspx?bib={target['bib']}&PagFis={target['pagfis']}&Pesq={target['query']}"
                item["url"] = url
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                try:
                    page.wait_for_selector("#form1", state="attached", timeout=90000)
                except Exception:
                    page.wait_for_timeout(10000)
                    page.reload(wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#form1", state="attached", timeout=90000)
                page.wait_for_selector("#TextoDigitadoBtn", state="attached", timeout=60000)
                item["button"] = True
                page.locator("#TextoDigitadoBtn").click(force=True, timeout=30000)
                deadline = time.time() + 55
                while time.time() < deadline:
                    label = page.locator("#TextoDigitadoNotification_C_TextoDigitadoLbl")
                    if label.count():
                        try:
                            item["text"] = clean(label.text_content(timeout=3000))
                        except Exception:
                            item["text"] = ""
                    if item["text"]:
                        break
                    page.wait_for_timeout(700)
                item["body"] = clean(page.locator("body").inner_text(timeout=10000))[:16000]
                item["status"] = "text-loaded" if item["text"] else "empty"
            except Exception as exc:
                item["status"] = "error"
                item["errors"].append(f"{type(exc).__name__}: {exc}")
            finally:
                page.close()
                (OUT / "ocr_popup_test.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(item, ensure_ascii=False), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
