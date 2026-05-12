#!/usr/bin/env python3
"""
fetch_paper.py — 이미 받아둔 로컬 PDF를 하네스 output 디렉터리에
slug 폴더로 정리한다.

다운로드는 셸(orchestrator)이 curl로 처리한다. 이 스크립트는:
- PDF에서 제목을 뽑아 slug 결정
- {output_dir}/{slug}/ 폴더 생성 (충돌 시 -2, -3 ...)
- 입력 PDF를 {paper_dir}/paper.pdf로 이동
- JSON {"slug", "paper_path", "paper_dir", "title"}을 stdout에 출력

DOI 케이스는 별도의 한 줄 helper(`resolve_doi`)로 PDF URL만 결정하고,
실제 다운로드는 역시 shell이 한다.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def slugify(text: str, max_len: int = 60) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s\-_]+", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return text[:max_len].rstrip("-") or "untitled"


def extract_title_from_pdf(pdf_path: Path) -> str:
    """PDF 메타 또는 첫 페이지의 가장 큰 폰트 라인에서 제목 추출.
    실패 시 파일명을 사용."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return pdf_path.stem
    try:
        doc = fitz.open(str(pdf_path))
        meta_title = (doc.metadata or {}).get("title", "").strip()
        if meta_title and len(meta_title) > 3:
            doc.close()
            return meta_title
        page = doc[0]
        blocks = page.get_text("dict")["blocks"]
        candidates = []
        for b in blocks:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if len(text) > 10:
                        candidates.append((span.get("size", 0), text))
        doc.close()
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
    except Exception:
        pass
    return pdf_path.stem


def main():
    parser = argparse.ArgumentParser(
        description="Local PDF -> slug folder under the harness output dir"
    )
    parser.add_argument("pdf", help="Local PDF path (already downloaded)")
    parser.add_argument(
        "--output",
        required=True,
        help="Output base directory (the harness 'output/' folder)",
    )
    args = parser.parse_args()

    src = Path(args.pdf).expanduser().resolve()
    if not src.exists():
        print(f"Error: PDF not found: {src}", file=sys.stderr)
        sys.exit(1)
    if src.suffix.lower() != ".pdf":
        print(f"Error: not a PDF file: {src}", file=sys.stderr)
        sys.exit(1)

    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)

    # slug 결정
    title = extract_title_from_pdf(src)
    slug = slugify(title)
    paper_dir = output_base / slug
    counter = 2
    while paper_dir.exists() and any(paper_dir.iterdir()):
        paper_dir = output_base / f"{slug}-{counter}"
        counter += 1
    paper_dir.mkdir(parents=True, exist_ok=True)

    # 최종 위치로 이동 (입력 PDF가 이미 paper_dir 안에 있으면 그대로 둠)
    final_pdf = paper_dir / "paper.pdf"
    if src != final_pdf:
        shutil.move(str(src), str(final_pdf))

    print(
        json.dumps(
            {
                "slug": paper_dir.name,
                "paper_path": str(final_pdf),
                "paper_dir": str(paper_dir),
                "title": title,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
