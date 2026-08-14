import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("hdb_results")
OUT.mkdir(exist_ok=True)

TARGETS = [
    {"bib": "028274_03", "pagfis": "117203", "query": "1165", "label": "1988-08-21-p49"},
    {"bib": "028274_03", "pagfis": "116342", "query": "1165", "label": "1988-07-31-p44"},
]


def clean(value):
    return (value or "").strip()


def main():
    report = {"targets": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
            locale="pt-BR", viewport={"width": 1600, "height": 1200}, ignore_https_errors=True,
        )
        for target in TARGETS:
            item = dict(target)
            item.update({"url": "", "button": False, "before": "", "after": "", "body": "", "responses": [], "errors": [], "status": "started"})
            report["targets"].append(item)
            page = context.new_page()
            page.on("pageerror", lambda exc, target_item=item: target_item["errors"].append(str(exc)))
            def response_listener(response, target_item=item):
                if response.request.method == "POST" or "Texto" in response.url or "XmlHttpPanel" in response.url:
                    target_item["responses"].append({
                        "method": response.request.method,
                        "url": response.url,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    })
            page.on("response", response_listener)
            try:
                url = f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={target['bib']}&pagfis={target['pagfis']}&Pesq={target['query']}"
                item["url"] = url
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_selector("#TextoDigitadoBtn", state="attached", timeout=90000)
                page.wait_for_timeout(1500)
                item["button"] = page.locator("#TextoDigitadoBtn").count() > 0
                label = page.locator("#TextoDigitadoNotification_C_TextoDigitadoLbl")
                if label.count():
                    item["before"] = clean(label.text_content(timeout=5000))
                page.locator("#TextoDigitadoBtn").click(force=True, timeout=30000)
                deadline = time.time() + 45
                latest = ""
                while time.time() < deadline:
                    if label.count():
                        try:
                            latest = clean(label.text_content(timeout=3000))
                        except Exception:
                            latest = ""
                    if latest:
                        break
                    page.wait_for_timeout(700)
                item["after"] = latest
                item["body"] = clean(page.locator("body").inner_text(timeout=10000))[:12000]
                item["status"] = "text-loaded" if latest else "empty"
                page.screenshot(path=str(OUT / f"ocr_popup_{target['label']}.png"), full_page=True, timeout=90000)
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
