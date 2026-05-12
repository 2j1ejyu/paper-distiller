#!/usr/bin/env python3
"""
finalize_paper.py — rename a paper output directory to a title-based slug
after the analysis loop reaches PASS.
"""
import argparse
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path


def _read_h1(analysis_md: Path) -> str | None:
    if not analysis_md.exists():
        return None
    for line in analysis_md.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def slugify(title: str, max_len: int = 80) -> str:
    if not title:
        return ""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", ascii_only)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if len(cleaned) > max_len:
        cut = cleaned[:max_len]
        last_hyphen = cut.rfind("-")
        if last_hyphen >= max_len // 2:
            cut = cut[:last_hyphen]
        cleaned = cut.strip("-")
    return cleaned


SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def main():
    parser = argparse.ArgumentParser(description="Rename paper dir to title slug")
    parser.add_argument("--paper-dir", required=True, help="Current paper directory")
    args = parser.parse_args()

    paper_dir = Path(args.paper_dir).resolve()
    parent = paper_dir.parent
    old_slug = paper_dir.name

    title = _read_h1(paper_dir / "analysis.md")
    if not title:
        print(json.dumps({
            "old_slug": old_slug, "new_slug": old_slug,
            "new_paper_dir": str(paper_dir),
            "reason": "no analysis.md or no H1 in analysis.md"
        }, ensure_ascii=False))
        return

    base_slug = slugify(title)
    if not base_slug or not SLUG_OK.match(base_slug):
        print(json.dumps({
            "old_slug": old_slug, "new_slug": old_slug,
            "new_paper_dir": str(paper_dir),
            "reason": f"slug invalid for title {title!r}; keeping old"
        }, ensure_ascii=False))
        return

    candidate = base_slug
    suffix = 2
    while (parent / candidate).exists() and (parent / candidate) != paper_dir:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
        if suffix > 99:
            print("Error: too many collisions", file=sys.stderr)
            sys.exit(1)

    new_dir = parent / candidate
    if new_dir == paper_dir:
        print(json.dumps({
            "old_slug": old_slug, "new_slug": candidate,
            "new_paper_dir": str(new_dir),
            "reason": "already at target slug"
        }, ensure_ascii=False))
        return

    shutil.move(str(paper_dir), str(new_dir))
    print(json.dumps({
        "old_slug": old_slug, "new_slug": candidate,
        "new_paper_dir": str(new_dir)
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
