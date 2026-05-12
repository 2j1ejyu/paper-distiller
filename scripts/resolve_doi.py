#!/usr/bin/env python3
"""
resolve_doi.py — DOI를 PDF 다운로드 URL로 변환.

CrossRef API에서 'application/pdf' 링크를 찾아 stdout에 URL만 출력한다.
없으면 fallback으로 랜딩 페이지 URL을 출력한다 (paywall 가능성 높음).

orchestrator가 이 스크립트의 stdout을 받아서 curl로 다운로드한다.
"""

import json
import sys
import urllib.parse
import urllib.request


def main():
    if len(sys.argv) != 2:
        print("Usage: resolve_doi.py <DOI>", file=sys.stderr)
        sys.exit(1)
    doi = sys.argv[1].strip()
    cr_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    req = urllib.request.Request(
        cr_url, headers={"User-Agent": "paper-harness/1.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data.get("message", {})
    for link in msg.get("link", []):
        if link.get("content-type") == "application/pdf":
            print(link.get("URL"))
            return
    fallback = msg.get("URL")
    if fallback:
        print(fallback)
        return
    print(f"Error: no URL resolved for DOI: {doi}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
