#!/usr/bin/env python3
"""
process_pdf.py — Single-pass docling extraction of text + figures.

Replaces the old split between parse_pdf.py (pymupdf text) and
extract_figures.py (docling figures), which required parsing the PDF twice
with two different backends. We now run docling once and emit both
parsed.json and figures/figures.json + figures/all/*.png from the same
DoclingDocument.

Outputs (in ``--output`` directory):
  parsed.json — {metadata, full_text, sections, captions}
  figures/figures.json — {extracted, unmatched, skipped}
  figures/all/figure_{N}.png — single-panel figures
  figures/all/figure_{N}_{a,b,...}.png — multi-panel figures
  figures/all/figure_unknown_{i}.png — pictures docling couldn't match to a caption

Caption resolution has two stages, in this order:
  1. docling's linked caption (``picture.caption_text(doc)``)
  2. fallback: geometric nearest-neighbour search among all caption items
     on the same page whose text starts with ``Figure N:`` / ``Fig. N.``.

Pictures that fail both stages but still have image data go into ``unmatched``
(image saved as ``figure_unknown_{i}.png``). The Writer subagent reconciles
these against the body text when it summarises the paper.

Multi-panel: when one Figure number matches multiple pictures on the SAME
page, the union of their bounding boxes is rendered from the source PDF as
a single composite ``figure_{N}.png`` (preserves author-intended spacing).
Fallback to per-panel ``figure_{N}_{a,b,...}.png`` when panels span pages
or the union bbox is dilated far beyond the panel area (likely captured
unrelated body content).
"""

import argparse
import json
import re
import sys
from pathlib import Path


# Matches a caption that starts with a Figure/Fig number followed by ':' or '.'.
# The separator avoids matching body sentences like "Figure 1 shows ...".
CAPTION_NUMBER_RE = re.compile(
    r"^\s*(?:Figure|Fig\.)\s+(\d+)\s*[:.]", re.IGNORECASE
)


def _lazy_imports():
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            LayoutOptions,
            PdfPipelineOptions,
        )
        from docling.datamodel.layout_model_specs import (
            DOCLING_LAYOUT_EGRET_XLARGE,
        )
    except ImportError as e:
        print(
            f"Error: missing dependency ({e}). Run: "
            "pip install -r requirements.txt --break-system-packages",
            file=sys.stderr,
        )
        sys.exit(1)
    return (
        DocumentConverter,
        PdfFormatOption,
        InputFormat,
        PdfPipelineOptions,
        LayoutOptions,
        DOCLING_LAYOUT_EGRET_XLARGE,
    )


def _build_converter():
    (
        DocumentConverter,
        PdfFormatOption,
        InputFormat,
        PdfPipelineOptions,
        LayoutOptions,
        DOCLING_LAYOUT_EGRET_XLARGE,
    ) = _lazy_imports()
    opts = PdfPipelineOptions()
    opts.generate_picture_images = True
    # images_scale is a multiplier over docling's baseline page rendering
    # (~72 DPI). 4.0 ≈ 288 DPI, matching the prior subagent's 300-DPI target.
    opts.images_scale = 4.0
    # HERON (default) misses sub-panels in multi-panel figures (e.g. Neural
    # ODE's Figure 1 right panel). EGRET_XLARGE catches them.
    opts.layout_options = LayoutOptions(
        model_spec=DOCLING_LAYOUT_EGRET_XLARGE,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
        }
    )


def _label_str(item):
    """Lowercase string form of an item's label, robust to enum/str variants."""
    label = getattr(item, "label", None)
    if hasattr(label, "value"):
        return str(label.value).lower()
    return str(label).lower()


def _prov(item):
    prov = getattr(item, "prov", None) or []
    return prov[0] if prov else None


def _page_of(item):
    p = _prov(item)
    if p is None:
        return None
    pn = getattr(p, "page_no", None)
    return int(pn) if pn is not None else None


def _bbox_of(item):
    p = _prov(item)
    return getattr(p, "bbox", None) if p is not None else None


def _bbox_center(bbox):
    return ((bbox.l + bbox.r) / 2.0, (bbox.t + bbox.b) / 2.0)


def _count_citations(text, number):
    if not text:
        return 0
    n = re.escape(str(number))
    pat = re.compile(rf"\b(?:Figure|Fig\.)\s+{n}\b", re.IGNORECASE)
    return len(pat.findall(text))


# ---- Text / sections / captions ---------------------------------------------

# Labels that contribute textual content to the body / sections.
_BODY_LABELS = {"text", "list_item", "code", "footnote"}
# Labels that are skipped entirely from text aggregation.
_NON_TEXT_LABELS = {"picture", "table", "page_header", "page_footer"}


def walk_text(doc):
    """Single pass over doc items: build sections, captions, full_text.

    - ``section_header`` items mark section boundaries; their text becomes the
      section name.
    - ``caption`` items are collected into a separate list (with page/bbox/number)
      so they don't pollute body_text used for citation counting.
    - ``text``/``list_item``/``code``/``footnote`` items go into the current
      section's body.
    - ``formula`` items have empty text in docling; their content lives in the
      page raster, so we skip them for the prose stream.
    - ``picture``/``table``/page header/footer are skipped from text.
    """
    sections = []
    captions = []
    full_text_parts = []
    current_section = "preamble"
    current_lines = []

    def flush():
        if current_lines:
            sections.append(
                {"name": current_section, "text": "\n".join(current_lines).strip()}
            )

    for item, _level in doc.iterate_items():
        label = _label_str(item)
        text = (getattr(item, "text", "") or "").strip()
        if not text or label in _NON_TEXT_LABELS or "formula" in label:
            continue

        full_text_parts.append(text)

        if "section_header" in label:
            flush()
            current_section = text
            current_lines = []
            continue

        if "caption" in label:
            m = CAPTION_NUMBER_RE.match(text)
            bbox = _bbox_of(item)
            page_no = _page_of(item)
            if m and bbox is not None and page_no is not None:
                captions.append(
                    {
                        "page": page_no,
                        "_bbox": bbox,  # used for geometric matching; not serialized
                        "text": text,
                        "number": int(m.group(1)),
                    }
                )
            continue

        if label.split(".")[-1] in _BODY_LABELS or "text" in label:
            current_lines.append(text)

    flush()
    return {
        "sections": sections,
        "captions": captions,
        "full_text": "\n".join(full_text_parts),
    }


def extract_metadata(doc):
    """Best-effort metadata pulled from the DoclingDocument."""
    meta = {"page_count": None, "filename": None}
    try:
        meta["page_count"] = len(doc.pages)
    except Exception:
        pass
    origin = getattr(doc, "origin", None)
    if origin is not None:
        try:
            d = origin.model_dump() if hasattr(origin, "model_dump") else dict(origin)
            meta["filename"] = d.get("filename")
            meta["mimetype"] = d.get("mimetype")
        except Exception:
            pass
    name = getattr(doc, "name", None)
    if name:
        meta["docling_name"] = str(name)
    return meta


# ---- Figures ----------------------------------------------------------------

def _picture_caption(picture, doc):
    """Return docling's linked caption text for a picture (may be empty)."""
    try:
        text = picture.caption_text(doc)
        if text:
            return text.strip()
    except Exception:
        pass
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


def _nearest_caption(captions, page_no, pbbox):
    """Among captions on the same page, return the one whose center is
    nearest to the picture's center. None if no candidates."""
    if page_no is None or pbbox is None:
        return None
    same_page = [c for c in captions if c["page"] == page_no]
    if not same_page:
        return None
    pcx, pcy = _bbox_center(pbbox)

    def d(c):
        cx, cy = _bbox_center(c["_bbox"])
        return ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5

    return min(same_page, key=d)


def _bbox_to_topleft(bbox, page_h):
    """Return (x0, y0, x1, y1) in PyMuPDF's TOPLEFT origin from a docling bbox."""
    origin = getattr(bbox, "coord_origin", None)
    name = getattr(origin, "name", None) if origin is not None else None
    if name is None and origin is not None:
        name = str(origin)
    if name and "TOP" in str(name).upper():
        return bbox.l, bbox.t, bbox.r, bbox.b
    # BOTTOMLEFT (PDF native): flip y about page height.
    return bbox.l, page_h - bbox.t, bbox.r, page_h - bbox.b


def _emit_merged(panels, number, pdf_path, out_dir, full_text):
    """Render the bbox-union of same-page panels from the source PDF page.

    Returns the extracted record, or None if panels span pages or the union
    bbox is dilated far beyond the panel area sum (likely capturing unrelated
    body content between widely-separated panels).
    """
    import fitz  # PyMuPDF; lazy import keeps module-level imports light

    pages = {p["page"] for p in panels}
    if len(pages) != 1:
        return None
    page_no = next(iter(pages))

    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_no - 1]
        page_h = page.rect.height

        rects = []
        for p in panels:
            x0, y0, x1, y1 = _bbox_to_topleft(p["bbox"], page_h)
            rects.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

        ux0 = min(r[0] for r in rects)
        uy0 = min(r[1] for r in rects)
        ux1 = max(r[2] for r in rects)
        uy1 = max(r[3] for r in rects)

        union_area = max(0.0, (ux1 - ux0) * (uy1 - uy0))
        panels_area = sum(max(0.0, (r[2] - r[0]) * (r[3] - r[1])) for r in rects)
        # Threshold: if the union is more than 2.5x the panel sum, the panels
        # are likely separated by body text. Fall back to per-panel emit.
        if panels_area <= 0 or union_area > 2.5 * panels_area:
            return None

        clip = fitz.Rect(ux0, uy0, ux1, uy1)
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            return None

        # Matrix(4,4) ≈ 288 DPI, matching docling's images_scale=4.0.
        pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, alpha=False)
        png_rel = f"figures/all/figure_{number}.png"
        png_path = out_dir / png_rel
        png_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(png_path))
        w, h = pix.width, pix.height
    finally:
        doc.close()

    caption = panels[0]["caption"]
    total = _count_citations(full_text, number)
    cap_hits = _count_citations(caption, number) if caption else 0
    body_citation_count = max(0, total - cap_hits)

    try:
        file_size = png_path.stat().st_size
    except OSError:
        file_size = 0

    return {
        "number": number,
        "number_str": str(number),
        "panel": None,
        "caption": caption,
        "page": page_no,
        "image_path": png_rel,
        "body_citation_count": body_citation_count,
        "width_px": w,
        "height_px": h,
        "file_size_bytes": file_size,
        "merged_from": len(panels),
    }


def _emit_panel(panel, number, suffix, out_dir, full_text):
    """Save panel PNG, return its extracted record."""
    if suffix is None:
        png_rel = f"figures/all/figure_{number}.png"
        number_str = str(number)
    else:
        png_rel = f"figures/all/figure_{number}_{suffix}.png"
        number_str = f"{number}{suffix}"

    png_path = out_dir / png_rel
    png_path.parent.mkdir(parents=True, exist_ok=True)
    pil = panel["pil_img"]
    with pil:
        w, h = pil.width, pil.height
        pil.save(png_path, format="PNG")

    caption = panel["caption"]
    total = _count_citations(full_text, number)
    cap_hits = _count_citations(caption, number) if caption else 0
    body_citation_count = max(0, total - cap_hits)

    try:
        file_size = png_path.stat().st_size
    except OSError:
        file_size = 0

    return {
        "number": number,
        "number_str": number_str,
        "panel": suffix,
        "caption": caption,
        "page": panel.get("page"),
        "image_path": png_rel,
        "body_citation_count": body_citation_count,
        "width_px": w,
        "height_px": h,
        "file_size_bytes": file_size,
    }


def _emit_unmatched(item, index, out_dir):
    """Save an unmatched picture as figure_unknown_{index}.png and return its record."""
    png_rel = f"figures/all/figure_unknown_{index}.png"
    png_path = out_dir / png_rel
    png_path.parent.mkdir(parents=True, exist_ok=True)
    pil = item["pil_img"]
    with pil:
        w, h = pil.width, pil.height
        pil.save(png_path, format="PNG")
    try:
        file_size = png_path.stat().st_size
    except OSError:
        file_size = 0
    return {
        "page": item["page"],
        "image_path": png_rel,
        "width_px": w,
        "height_px": h,
        "file_size_bytes": file_size,
    }


def extract_figures(doc, captions, full_text, out_dir, pdf_path):
    pictures = list(getattr(doc, "pictures", []) or [])
    print(f"docling found {len(pictures)} pictures", file=sys.stderr)

    candidates = []
    unmatched_items = []
    skipped = []

    for picture in pictures:
        page_no = _page_of(picture)
        pbbox = _bbox_of(picture)
        if pbbox is None:
            skipped.append(
                {"number": None, "page": page_no, "reason": "picture has no bbox"}
            )
            continue

        linked = _picture_caption(picture, doc)
        m = CAPTION_NUMBER_RE.match(linked or "")
        number = None
        caption_text = None
        if m:
            number = int(m.group(1))
            caption_text = linked
        else:
            chosen = _nearest_caption(captions, page_no, pbbox)
            if chosen is not None:
                number = chosen["number"]
                caption_text = chosen["text"]

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

        if number is None:
            # Caption matching failed — keep the image so the Writer can
            # reconcile it against the body text later.
            unmatched_items.append(
                {"page": page_no, "bbox": pbbox, "pil_img": pil_img}
            )
            continue

        candidates.append(
            {
                "number": number,
                "caption": caption_text,
                "page": page_no,
                "bbox": pbbox,
                "pil_img": pil_img,
            }
        )

    groups = {}
    for c in candidates:
        groups.setdefault(c["number"], []).append(c)

    extracted = []
    for number in sorted(groups.keys()):
        panels = groups[number]
        if len(panels) == 1:
            extracted.append(_emit_panel(panels[0], number, None, out_dir, full_text))
            continue

        merged = _emit_merged(panels, number, pdf_path, out_dir, full_text)
        if merged is not None:
            extracted.append(merged)
            for p in panels:
                try:
                    p["pil_img"].close()
                except Exception:
                    pass
            continue

        # Fallback: panels span pages or union bbox is too dilated — emit
        # per-panel a/b/c top-to-bottom then left-to-right (BOTTOMLEFT origin).
        panels_sorted = sorted(
            panels, key=lambda x: (-x["bbox"].t, x["bbox"].l)
        )
        for i, p in enumerate(panels_sorted):
            suffix = chr(ord("a") + i)
            extracted.append(_emit_panel(p, number, suffix, out_dir, full_text))

    unmatched = []
    for i, item in enumerate(unmatched_items, start=1):
        unmatched.append(_emit_unmatched(item, i, out_dir))

    return extracted, unmatched, skipped


# ---- Main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Single-pass docling extraction of text + figures from a PDF"
    )
    ap.add_argument("pdf", help="Path to paper.pdf")
    ap.add_argument("--output", required=True, help="Output paper directory")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "figures" / "all").mkdir(parents=True, exist_ok=True)

    converter = _build_converter()
    try:
        result = converter.convert(str(pdf_path))
    except Exception as e:
        print(f"Error: docling conversion failed: {e}", file=sys.stderr)
        sys.exit(1)
    doc = result.document

    metadata = extract_metadata(doc)
    txt = walk_text(doc)

    parsed = {
        "metadata": metadata,
        "full_text": txt["full_text"],
        "sections": txt["sections"],
        "captions": [
            {k: v for k, v in c.items() if not k.startswith("_")}
            for c in txt["captions"]
        ],
    }
    parsed_path = out_dir / "parsed.json"
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    extracted, unmatched, skipped = extract_figures(
        doc, txt["captions"], txt["full_text"], out_dir, pdf_path
    )
    figures_json_path = out_dir / "figures" / "figures.json"
    with open(figures_json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"extracted": extracted, "unmatched": unmatched, "skipped": skipped},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        json.dumps(
            {
                "parsed_path": str(parsed_path),
                "figures_json": str(figures_json_path),
                "section_count": len(txt["sections"]),
                "caption_count": len(txt["captions"]),
                "page_count": metadata.get("page_count"),
                "extracted": len(extracted),
                "unmatched": len(unmatched),
                "skipped": len(skipped),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
