#!/usr/bin/env python3
"""
parse_pdf.py — PDF에서 텍스트·섹션·메타만 추출.

산출물:
  {output}/parsed.json — metadata, full_text, sections

피규어 추출은 이 스크립트가 하지 않는다. SKILL.md Step 2b에서
scripts/extract_figures.py가 docling으로 처리한다.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def _lazy_imports():
    try:
        import fitz  # noqa: F401
    except ImportError as e:
        print(
            f"Error: missing dependency ({e}). Run: "
            "pip install -r requirements.txt --break-system-packages",
            file=sys.stderr,
        )
        sys.exit(1)
    return fitz


SECTION_HEADERS = re.compile(
    r"^(\d+\.?\s+)?(Abstract|Introduction|Related Work|Background|"
    r"Method(?:s|ology)?|Approach|Experiment[s]?|Result[s]?|"
    r"Discussion|Analysis|Conclusion[s]?|References?)\b",
    re.IGNORECASE,
)


def extract_metadata(doc):
    meta = doc.metadata or {}
    return {
        "title": meta.get("title", "").strip() or None,
        "author": meta.get("author", "").strip() or None,
        "subject": meta.get("subject", "").strip() or None,
        "page_count": len(doc),
    }


def split_into_sections(full_text):
    lines = full_text.split("\n")
    sections = []
    current_name = "preamble"
    current_lines = []
    for line in lines:
        m = SECTION_HEADERS.match(line.strip())
        if m and len(line.strip()) < 80:
            if current_lines:
                sections.append(
                    {"name": current_name, "text": "\n".join(current_lines).strip()}
                )
            current_name = m.group(2)
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append(
            {"name": current_name, "text": "\n".join(current_lines).strip()}
        )
    return sections


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDF text+sections into parsed.json (no figures)"
    )
    parser.add_argument("pdf", help="Path to paper.pdf")
    parser.add_argument("--output", required=True, help="Output paper directory")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    fitz = _lazy_imports()
    fdoc = fitz.open(str(pdf_path))
    metadata = extract_metadata(fdoc)

    full_text_parts = [page.get_text() for page in fdoc]
    full_text = "\n".join(full_text_parts)
    sections = split_into_sections(full_text)
    fdoc.close()

    parsed = {
        "metadata": metadata,
        "full_text": full_text,
        "sections": sections,
    }

    out_json = out_dir / "parsed.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "parsed_path": str(out_json),
                "section_count": len(sections),
                "page_count": metadata["page_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
