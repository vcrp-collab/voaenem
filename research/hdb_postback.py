import json
import urllib.request
from pathlib import Path

OUT = Path('hdb_results')
OUT.mkdir(exist_ok=True)

ISSUES = [
    ('1988', '09236'),
    ('1988', '09257'),
    ('1988', '09264'),
    ('1988', '09306'),
    ('1988', '09362'),
    ('1988', '09369'),
    ('1989', '09453'),
]


def main():
    report = {'tests': []}
    for year, issue in ISSUES:
        variants = [
            f'https://memoria.bn.gov.br/pdf/028274_03/per028274_03_{year}_{issue}.pdf',
            f'https://memoria.bn.gov.br/pdf/028274_03/per028274_03_{year}_{int(issue)}.pdf',
            f'http://memoria.bn.br/pdf/028274_03/per028274_03_{year}_{issue}.pdf',
        ]
        for index, url in enumerate(variants, 1):
            item = {'year': year, 'issue': issue, 'url': url, 'status': '', 'content_type': '', 'length': 0, 'file': '', 'error': ''}
            report['tests'].append(item)
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/pdf,*/*'})
                with urllib.request.urlopen(request, timeout=90) as response:
                    data = response.read()
                    item['status'] = getattr(response, 'status', 200)
                    item['content_type'] = response.headers.get('Content-Type', '')
                    item['length'] = len(data)
                    if data.startswith(b'%PDF'):
                        path = OUT / f'correio_{year}_{issue}.pdf'
                        path.write_bytes(data)
                        item['file'] = str(path)
                        break
            except Exception as exc:
                item['error'] = f'{type(exc).__name__}: {exc}'
    (OUT / 'pdf_routes.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
