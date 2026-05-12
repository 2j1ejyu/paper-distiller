#!/usr/bin/env python3
"""
extract_figures.py — Extract figures from a paper PDF using docling.

Reads ``<output-dir>/parsed.json`` (produced by parse_pdf.py) to compute
``body_citation_count`` for each figure. Saves PNGs to
``<output-dir>/figures/all/figure_{N}.png`` and writes a contract-compliant
``<output-dir>/figures/figures.json`` consumed by the renderer downstream.

The script is deterministic: one pass, no retries, no per-PDF tuning. If
docling fails to convert the PDF, the script exits non-zero with no output
file written — the orchestrator handles fallback.
"""

import argparse
import json
import re
import sys
from pathlib import Path


# Matches a caption that starts with a Figure/Fig number, e.g. "Figure 1:",
# "Fig. 3 -", "figure 12.". The captured group is the paper-authored number.
CAPTION_NUMBER_RE = re.compile(r"^\s*(?:Figure|Fig\.)\s+(\d+)\b", re.IGNORECASE)


def _lazy_imports():
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except ImportError as e:
        print(
            f"Error: missing dependency ({e}). Run: "
            "pip install -r requirements.txt --break-system-packages",
            file=sys.stderr,
        )
        sys.exit(1)
    return DocumentConverter, PdfFormatOption, InputFormat, PdfPipelineOptions


def _build_converter():
    DocumentConverter, PdfFormatOption, InputFormat, PdfPipelineOptions = (
        _lazy_imports()
    )
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_picture_images = True
    # images_scale is a multiplier over docling's baseline page rendering
    # (~72 DPI). 4.0 ≈ 288 DPI, matching the prior subagent's 300-DPI target.
    pipeline_options.images_scale = 4.0
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def _get_caption_text(picture, doc):
    """Return caption text for a picture, robust to docling version quirks."""
    # Preferred path: docling 2.x convenience method.
    try:
        text = picture.caption_text(doc)
        if text:
            return text.strip()
    except Exception:
        pass
    # Fallback: resolve each caption ref manually.
    parts = []
    for ref in getattr(picture, "captions", []) or []:
        try:
            item = ref.resolve(doc)
            t = getattr(item, "text", None)
            if t:
                parts.append(t)
        except Exception:
            continue
    return " ".join(parts).strip()


def _get_page_no(picture):
    prov = getattr(picture, "prov", None) or []
    if prov:
        page_no = getattr(prov[0], "page_no", None)
        if page_no is not None:
            return int(page_no)
    return None


def _count_body_citations(text, number):
    """Count references to ``Figure N`` / ``Fig. N`` (case-insensitive)."""
    if not text:
        return 0
    n = re.escape(str(number))
    pattern = re.compile(rf"\b(?:Figure|Fig\.)\s+{n}\b", re.IGNORECASE)
    return len(pattern.findall(text))


def _load_full_text(parsed_path):
    if not parsed_path.exists():
        print(
            f"Warning: {parsed_path} not found; body_citation_count will be 0",
            file=sys.stderr,
        )
        return ""
    try:
        with open(parsed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("full_text", "") or ""
    except Exception as e:
        print(f"Warning: failed to read parsed.json: {e}", file=sys.stderr)
        return ""


def _dedup_by_number(candidates):
    """Keep the candidate with the longest caption per figure number.

    Deterministic tiebreakers when caption lengths tie:
      1. Lower page number wins (None pages sort last).
      2. Earlier original index in ``candidates`` wins.

    Returns (kept_dict_by_number, dropped_list).
    """
    # Sort by (number asc, caption length desc, page asc with None last,
    # original index asc) so that the first occurrence per number is the
    # preferred winner under "first wins" semantics.
    INF_PAGE = float("inf")
    ordered = sorted(
        enumerate(candidates),
        key=lambda iv: (
            iv[1]["number"],
            -len(iv[1]["caption"]),
            iv[1].get("page") if iv[1].get("page") is not None else INF_PAGE,
            iv[0],
        ),
    )

    kept = {}
    dropped = []
    for _orig_idx, cand in ordered:
        num = cand["number"]
        if num not in kept:
            kept[num] = cand
        else:
            dropped.append(
                {
                    "number": cand["number"],
                    "page": cand.get("page"),
                    "reason": "duplicate figure number, shorter caption",
                }
            )
    return kept, dropped


def _write_output(figures_json_path, extracted, skipped):
    payload = {"extracted": extracted, "skipped": skipped}
    with open(figures_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Extract figures from PDF via docling -> figures/figures.json"
    )
    parser.add_argument("--pdf", required=True, help="Path to paper.pdf")
    parser.add_argument(
        "--output-dir", required=True, help="Paper output directory"
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = Path(args.output_dir)
    figures_dir = out_dir / "figures"
    all_dir = figures_dir / "all"
    all_dir.mkdir(parents=True, exist_ok=True)
    figures_json_path = figures_dir / "figures.json"

    # Run docling conversion. On failure, exit non-zero WITHOUT writing
    # figures.json — the orchestrator should detect and fall back.
    converter = _build_converter()
    try:
        result = converter.convert(str(pdf_path))
    except Exception as e:
        print(f"Error: docling conversion failed: {e}", file=sys.stderr)
        sys.exit(1)

    doc = result.document
    pictures = list(getattr(doc, "pictures", []) or [])
    print(f"docling found {len(pictures)} pictures", file=sys.stderr)

    full_text = _load_full_text(out_dir / "parsed.json")

    candidates = []  # successful caption parses; not yet deduped
    skipped = []    # skip reasons accumulated

    for picture in pictures:
        caption = _get_caption_text(picture, doc)
        page_no = _get_page_no(picture)

        m = CAPTION_NUMBER_RE.match(caption or "")
        if not m:
            skipped.append(
                {
                    "number": None,
                    "page": page_no,
                    "reason": "caption pattern not matched",
                }
            )
            continue
        number = int(m.group(1))

        try:
            pil_img = picture.get_image(doc)
        except Exception as e:
            print(
                f"Warning: get_image failed for figure {number}: {e}",
                file=sys.stderr,
            )
            pil_img = None
        if pil_img is None:
            skipped.append(
                {"number": number, "page": page_no, "reason": "no image data"}
            )
            continue

        candidates.append(
            {
                "number": number,
                "caption": caption,
                "page": page_no,
                "pil_img": pil_img,
            }
        )

    # Dedup by figure number, keeping the longer caption.
    kept_by_num, dup_dropped = _dedup_by_number(candidates)
    skipped.extend(dup_dropped)

    # Persist kept images to disk and build final extracted records.
    extracted = []
    for number in sorted(kept_by_num.keys()):
        cand = kept_by_num[number]
        png_rel = f"figures/all/figure_{number}.png"
        png_path = out_dir / png_rel
        pil_img = cand["pil_img"]
        # Ensure parent directory still exists (defensive).
        png_path.parent.mkdir(parents=True, exist_ok=True)
        # Capture dimensions, save, then close to free pixmap memory.
        with pil_img:
            width_px, height_px = pil_img.width, pil_img.height
            pil_img.save(png_path, format="PNG")

        caption = cand["caption"]
        # Compute body_citation_count by counting on full_text and subtracting
        # any matches contained in the caption itself. Subtraction is robust
        # to whitespace/tokenization differences between docling's caption
        # text and how the caption appears in parsed.json's full_text.
        total = _count_body_citations(full_text, number)
        caption_hits = _count_body_citations(caption, number) if caption else 0
        body_citation_count = max(0, total - caption_hits)

        try:
            file_size_bytes = png_path.stat().st_size
        except OSError:
            file_size_bytes = 0

        extracted.append(
            {
                "number": number,
                "number_str": str(number),
                "caption": caption,
                "page": cand.get("page"),
                "image_path": png_rel,
                "body_citation_count": body_citation_count,
                "width_px": width_px,
                "height_px": height_px,
                "file_size_bytes": file_size_bytes,
            }
        )

    _write_output(figures_json_path, extracted, skipped)

    print(
        json.dumps(
            {
                "extracted": len(extracted),
                "skipped": len(skipped),
                "figures_json": str(figures_json_path.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
