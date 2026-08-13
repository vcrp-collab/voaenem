import json
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

OUT = Path("hdb_quick_results")
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


def main():
    result = {"started": time.time(), "targets": [], "errors": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Mobile Safari/537.36",
            locale="pt-BR",
            viewport={"width": 1280, "height": 1800},
            screen={"width": 1280, "height": 1800},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
            ignore_https_errors=True,
        )

        for target in TARGETS:
            item = dict(target)
            stem = f"{target['bib']}_{target['date']}_{target['issue']}_{target['pagfis']}_p{target['page']}"
            url = f"https://memoria.bn.gov.br/DocReader/DocReaderMobile.aspx?bib={target['bib']}&PagFis={target['pagfis']}&Pesq=2481165"
            item.update({"url": url, "final_url": "", "title": "", "images": [], "network_images": [], "links": [], "body_text": "", "screenshot": "", "html": "", "error": ""})
            result["targets"].append(item)
            page = context.new_page()
            page.set_default_timeout(30000)

            def on_response(response):
                ct = response.headers.get("content-type", "").lower()
                u = response.url
                if ct.startswith("image/") or any(x in u.lower() for x in [".jpg", ".jpeg", ".png", "documento", "imagem"]):
                    item["network_images"].append({"url": u, "status": response.status, "content_type": ct})

            page.on("response", on_response)
            try:
                print(f"\n=== {url} ===", flush=True)
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(10000)
                item["final_url"] = page.url
                item["title"] = page.title()
                item["body_text"] = (page.locator("body").inner_text(timeout=10000) or "")[:10000]
                item["links"] = page.locator("a").evaluate_all("els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href})).filter(x => x.href)")
                imgs = page.locator("img")
                count = imgs.count()
                for i in range(count):
                    loc = imgs.nth(i)
                    info = {
                        "index": i,
                        "id": loc.get_attribute("id") or "",
                        "alt": loc.get_attribute("alt") or "",
                        "src": loc.get_attribute("src") or "",
                        "natural_width": 0,
                        "natural_height": 0,
                        "file": "",
                        "screenshot": "",
                        "errors": [],
                    }
                    try:
                        size = loc.evaluate("e => ({w:e.naturalWidth||0,h:e.naturalHeight||0})")
                        info["natural_width"] = size.get("w", 0)
                        info["natural_height"] = size.get("h", 0)
                    except Exception as exc:
                        info["errors"].append(f"size {type(exc).__name__}: {exc}")
                    src = info["src"]
                    if src and (info["natural_width"] > 400 or info["natural_height"] > 400):
                        try:
                            response = context.request.get(urljoin(page.url, src), timeout=90000, headers={"Referer": page.url})
                            if response.ok:
                                ct = response.headers.get("content-type", "")
                                ext = ".jpg" if "jpeg" in ct else ".png" if "png" in ct else ".bin"
                                path = IMG / f"{stem}_img{i}{ext}"
                                path.write_bytes(response.body())
                                info["file"] = str(path)
                            else:
                                info["errors"].append(f"HTTP {response.status}")
                        except Exception as exc:
                            info["errors"].append(f"download {type(exc).__name__}: {exc}")
                        try:
                            shot = IMG / f"{stem}_img{i}_element.png"
                            loc.screenshot(path=str(shot), timeout=90000)
                            info["screenshot"] = str(shot)
                        except Exception as exc:
                            info["errors"].append(f"screenshot {type(exc).__name__}: {exc}")
                    item["images"].append(info)

                shot = IMG / f"{stem}_full.png"
                page.screenshot(path=str(shot), full_page=True, timeout=90000)
                item["screenshot"] = str(shot)
                html = HTML / f"{stem}.html"
                html.write_text(page.content(), encoding="utf-8")
                item["html"] = str(html)
                print(json.dumps({"date": item["date"], "final_url": item["final_url"], "title": item["title"], "images": item["images"], "network_images": item["network_images"][-15:]}, ensure_ascii=False), flush=True)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
                result["errors"].append({"target": target, "error": item["error"], "traceback": traceback.format_exc()})
                print(traceback.format_exc(), flush=True)
            finally:
                page.close()
                (OUT / "mobile.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        browser.close()
    result["finished"] = time.time()
    (OUT / "mobile.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
