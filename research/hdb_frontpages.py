import json
import ssl
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

OUT = Path("hdb_results")
IMG = OUT / "frontpages"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

TARGETS = [
    {"bib":"028274_03","issue":"09243","pagfis":"116571","label":"1988_issue09243"},
    {"bib":"028274_03","issue":"09257","pagfis":"117155","label":"1988_issue09257"},
    {"bib":"028274_03","issue":"09523","pagfis":"128514","label":"1989_issue09523"},
    {"bib":"028274_03","issue":"09565","pagfis":"130588","label":"1989_issue09565"},
    {"bib":"028274_03","issue":"09628","pagfis":"133632","label":"1989_issue09628"},
    {"bib":"028274_04","issue":"10272","pagfis":"27375","label":"1991_issue10272"},
    {"bib":"028274_04","issue":"1991_anchor","pagfis":"30078","label":"1991_1991-07-27_p30"},
    {"bib":"028274_04","issue":"09779","pagfis":"1258","label":"1990_issue09779"}
]

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def state(driver):
    return driver.execute_script("""
        const img=document.getElementById('DocumentoImg');
        const pasta=document.getElementById('PastaTxt');
        const pag=document.getElementById('PagAtualTxt');
        const pf=document.getElementById('hPagFis');
        return {
            img: img ? (img.src || '') : '',
            width: img ? (img.naturalWidth || 0) : 0,
            height: img ? (img.naturalHeight || 0) : 0,
            folder: pasta ? ((pasta.title || pasta.textContent || '').trim()) : '',
            page: pag ? (pag.value || '') : '',
            pagfis: pf ? (pf.value || '') : '',
            body: document.body ? document.body.innerText.slice(0,2000) : ''
        };
    """)


def wait_for_image(driver, timeout=75):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = state(driver)
            if last.get("img") and last.get("width", 0) > 400:
                return last
        except Exception:
            pass
        time.sleep(0.5)
    return last


def download_image(driver, src, path):
    cookies = "; ".join(f"{c['name']}={c['value']}" for c in driver.get_cookies())
    request = urllib.request.Request(src)
    request.add_header("Cookie", cookies)
    request.add_header("User-Agent", driver.execute_script("return navigator.userAgent"))
    request.add_header("Referer", driver.current_url)
    request.add_header("Accept", "image/*,*/*;q=0.8")
    with urllib.request.urlopen(request, context=SSL_CTX, timeout=90) as response:
        data = response.read()
        path.write_bytes(data)
        return {"status": getattr(response, "status", 200), "content_type": response.headers.get("Content-Type", ""), "bytes": len(data)}


def main():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1400")
    options.add_argument("--ignore-certificate-errors")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(120)
    report = {"targets": []}
    try:
        for target in TARGETS:
            item = dict(target)
            item.update({"url":"", "state":{}, "file":"", "download":{}, "error":""})
            report["targets"].append(item)
            try:
                url = f"https://memoria.bn.gov.br/DocReader/DocReader.aspx?bib={target['bib']}&pagfis={target['pagfis']}"
                item["url"] = url
                driver.get(url)
                time.sleep(5)
                try:
                    driver.execute_script("var w=window.$find&&$find('PesqOpniaoRadWindow'); if(w) w.close();")
                except Exception:
                    pass
                try:
                    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "DocumentoImg")))
                except Exception:
                    pass
                current = wait_for_image(driver)
                item["state"] = current
                src = current.get("img", "")
                if not src:
                    raise RuntimeError("DocumentoImg não carregou")
                ext = ".jpg" if "jpg" in src.lower() or "jpeg" in src.lower() else ".png"
                path = IMG / f"{target['label']}{ext}"
                item["download"] = download_image(driver, src, path)
                item["file"] = str(path)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                (OUT / "frontpages.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(item, ensure_ascii=False), flush=True)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
