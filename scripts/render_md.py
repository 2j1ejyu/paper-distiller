#!/usr/bin/env python3
r"""
render_md.py — analysis.md를 HTML로 렌더하고 페이지별 PNG 스크린샷 생성.

파이프라인:
  .md  --pandoc(--katex)-->  analysis.html  (KaTeX CDN <script>가 head에 박힘)
  .html --Playwright/Chromium--> analysis.pdf  (브라우저가 KaTeX 실행 후 print)
  .pdf --PyMuPDF--> render/page_1.png, page_2.png, ...

이전엔 WeasyPrint + pandoc --mathml 조합이었는데, WeasyPrint가 시스템 수학 폰트
(STIX/Latin Modern Math)를 못 찾으면 `\mathcal{L}`·`\mathbb{E}` 같은 special-font
글리프가 sans-serif fallback으로 깨졌다. Playwright는 Chromium이 KaTeX를 JS로
실행해서 KaTeX 번들 폰트로 직접 렌더하기 때문에 시스템 폰트가 없어도 정상 출력.

산출물:
  {output}/analysis.html
  {output}/analysis.pdf       (중간물)
  {output}/render/page_N.png

Render Evaluator가 page_N.png를 멀티모달로 본다.
"""

import argparse
import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path


CSS = """
@page { size: A4; margin: 2.5cm; }
body {
    font-family: -apple-system, "Helvetica Neue", "Noto Sans KR", sans-serif;
    line-height: 1.5;
    color: #222;
    margin: 0 auto;
    font-size: 11pt;
    box-sizing: border-box;
}
/* 인쇄/PDF: body는 @page margin이 만든 콘텐츠 영역을 가득 채운다 (일반 논문 외관). */
@media print {
    body { max-width: none; }
}
/* 화면에서는 종이 느낌의 카드를 가운데 정렬. 폭은 A4 콘텐츠 영역 즈음. */
@media screen {
    html { background: #eee; }
    body {
        max-width: 760px;
        background: white;
        padding: 3em 4em;
        box-shadow: 0 0 12px rgba(0,0,0,.08);
        margin: 2em auto;
    }
}
h1 { font-size: 1.6em; border-bottom: 2px solid #333; padding-bottom: 0.2em; }
h2 { font-size: 1.3em; margin-top: 1.5em; border-bottom: 1px solid #ccc; padding-bottom: 0.1em; }
h3 { font-size: 1.1em; margin-top: 1em; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
img + em, figcaption { display: block; text-align: center; font-size: 0.9em; color: #666; margin-top: -0.5em; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.4em 0.7em; text-align: left; }
th { background: #f5f5f5; }
code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
pre { background: #f4f4f4; padding: 0.7em; border-radius: 4px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 1em; color: #555; }
/* 긴 display 수식은 사전 단계에서 aligned 환경으로 줄바꿈 처리하므로
   여기서는 KaTeX의 기본 디스플레이 동작만 살린다. */
.katex-display { margin: 1em 0; overflow-x: visible; }
.katex { font-size: 1.0em; }
"""


def have_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# --- Long display-math line breaking ---------------------------------------
#
# KaTeX has no automatic line breaking for display math, so a single long
# `$$...$$` overflows the narrow body column and gets clipped at the page
# margin. We pre-process the markdown: long $$ blocks are wrapped in an
# `aligned` environment with manual `\\` breaks at top-level `=` (highest
# priority) and at top-level `+`/`-` (fallback for long RHS).

_STRUCTURED_ENVS = (
    r"\begin{aligned}", r"\begin{align}", r"\begin{align*}",
    r"\begin{matrix}", r"\begin{cases}",
    r"\begin{multline}", r"\begin{gather}", r"\begin{eqnarray}",
    r"\begin{split}", r"\begin{array}",
)


def _top_level_op_positions(s: str, ops: tuple[str, ...]) -> list[int]:
    """Find positions of operator chars in `s` that are at the top of LaTeX
    grouping (not inside `()`, `{}`, `[]`). LaTeX commands like `\\theta`
    are skipped so an `e` inside `\\le` is not mistaken for a `=`."""
    positions: list[int] = []
    depth = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            # skip a LaTeX command (\name) or a one-char escape (\,, \!, \\, \|)
            j = i + 1
            if j < len(s):
                if s[j].isalpha():
                    while j < len(s) and s[j].isalpha():
                        j += 1
                else:
                    j += 1
            i = j
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth = max(0, depth - 1)
        elif depth == 0 and c in ops:
            positions.append(i)
        i += 1
    return positions


def _split_at_positions(s: str, positions: list[int]) -> list[str]:
    """Split `s` so that each chunk after the first starts with the operator
    char that lives at the corresponding position."""
    if not positions:
        return [s]
    chunks: list[str] = []
    prev = 0
    for pos in positions:
        chunks.append(s[prev:pos])
        prev = pos
    chunks.append(s[prev:])
    return [c.strip() for c in chunks if c.strip()]


def _wrap_aligned(segments: list[str]) -> str:
    """Build a `\\begin{aligned}...\\end{aligned}` body from segments.
    Each segment after the first is expected to start with an operator
    (`=`, `+`, `-`). The first segment may be a bare LHS or also start
    with an operator."""
    lines: list[str] = []
    segs = list(segments)

    first = segs.pop(0)
    if first and first[0] == "=":
        rest = first[1:].lstrip()
        lines.append(f"& = {rest}")
    elif first and first[0] in "+-":
        op, rest = first[0], first[1:].lstrip()
        lines.append(f"& {op} {rest}")
    else:
        # Bare LHS: try to merge with the next `=`-segment so the alignment
        # column lands on `=` (the natural place).
        if segs and segs[0].startswith("="):
            rhs = segs.pop(0)[1:].lstrip()
            lines.append(f"{first} &= {rhs}")
        else:
            lines.append(first)

    for seg in segs:
        if not seg:
            continue
        op, rest = (seg[0], seg[1:].lstrip()) if seg[0] in "=+-" else ("", seg)
        if op == "=":
            lines.append(f"&= {rest}")
        elif op in "+-":
            lines.append(f"&\\quad {op} {rest}")
        else:
            lines.append(f"& {rest}")

    return "\\begin{aligned}\n" + " \\\\\n".join(lines) + "\n\\end{aligned}"


def _maybe_break_display_math(body: str, max_chars: int) -> str | None:
    """Return a wrapped (broken) version of `body` if it's too long and
    splittable; otherwise None (caller keeps the original)."""
    body = body.strip()
    if len(body) <= max_chars:
        return None
    if any(env in body for env in _STRUCTURED_ENVS):
        return None  # author already structured this; don't second-guess

    # Priority 1: split at every top-level `=`
    eq_positions = _top_level_op_positions(body, ("=",))
    segments = _split_at_positions(body, eq_positions) if eq_positions else [body]

    # Priority 2: any segment still longer than max_chars → split at +/- inside it
    final_segments: list[str] = []
    for seg in segments:
        if len(seg) <= max_chars:
            final_segments.append(seg)
            continue
        # Strip a leading op so the operator stays attached to the first
        # sub-segment after splitting.
        head_op = ""
        search = seg
        if seg and seg[0] in "=+-":
            head_op = seg[0]
            search = seg[1:].lstrip()
        sub_positions = _top_level_op_positions(search, ("+", "-"))
        if not sub_positions:
            final_segments.append(seg)
            continue
        sub_segments = _split_at_positions(search, sub_positions)
        if head_op:
            sub_segments[0] = head_op + " " + sub_segments[0]
        final_segments.extend(sub_segments)

    if len(final_segments) <= 1:
        return None  # nothing to break at; leave it (will overflow visibly)
    return _wrap_aligned(final_segments)


# Match $$ ... $$ (display math) — non-greedy, allows newlines, doesn't
# eat `$` inside which would mean we're still inside the same block.
_DISPLAY_MATH_RE = re.compile(r"\$\$\s*(.+?)\s*\$\$", re.DOTALL)


def break_long_display_math(md_text: str, max_chars: int = 110) -> str:
    """Pre-process markdown: rewrite long $$...$$ blocks as aligned with
    line breaks at = (and at top-level +/- if needed)."""
    def repl(m: re.Match) -> str:
        wrapped = _maybe_break_display_math(m.group(1), max_chars)
        if wrapped is None:
            return m.group(0)
        return f"$$\n{wrapped}\n$$"
    return _DISPLAY_MATH_RE.sub(repl, md_text)


def render_md_to_html(md_path: Path, html_path: Path, base_dir: Path) -> None:
    """pandoc으로 .md → 단독 HTML. CSS는 <style>로 inline, 수식은 --katex로 CDN 주입.
    긴 display 수식은 pandoc 호출 전에 aligned 환경으로 줄바꿈한다."""
    if not have_command("pandoc"):
        raise RuntimeError(
            "pandoc not found. Install with: brew install pandoc (or apt/dnf install pandoc)"
        )

    head_path = html_path.parent / "_render_head.html"
    head_path.write_text(f"<style>\n{CSS}\n</style>\n")

    # 원본 .md를 preprocessing해서 임시 파일로 pandoc에 넘긴다 (사용자 .md는 보존).
    raw = md_path.read_text(encoding="utf-8")
    cooked = break_long_display_math(raw)
    preprocessed = html_path.parent / "_render_preprocessed.md"
    preprocessed.write_text(cooked, encoding="utf-8")

    cmd = [
        "pandoc",
        str(preprocessed),
        "-o",
        str(html_path),
        "--standalone",
        # --katex=<URL>: KaTeX CSS/JS의 base URL을 명시. Debian/Ubuntu pandoc
        # 패키지는 기본값을 로컬 /usr/share/javascript/katex/로 바꿔놓는데 그
        # 경로에 파일이 없는 환경이 대부분이라 KaTeX 스크립트가 404로 죽고
        # 수식이 raw LaTeX로 남는다. CDN을 명시해서 항상 같은 결과를 보장.
        "--katex=https://cdn.jsdelivr.net/npm/katex@0.16.21/dist/",
        "-H",
        str(head_path),
        "--resource-path",
        str(base_dir),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"pandoc failed: {result.stderr}")
    finally:
        head_path.unlink(missing_ok=True)
        preprocessed.unlink(missing_ok=True)


async def _html_to_pdf_async(html_path: Path, pdf_path: Path) -> None:
    """Playwright Chromium 헤드리스로 HTML → PDF."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "playwright not installed. Run: "
            "pip install playwright && playwright install chromium"
        ) from e

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        # file:// URL로 로컬 HTML 로드. KaTeX/auto-render CDN 다운로드까지 기다리려고
        # networkidle 사용. CDN이 끝나면 pandoc이 박은 onload="renderMathInElement(...)"가
        # 발화해 수식 DOM이 치환된다.
        await page.goto(f"file://{html_path.resolve()}", wait_until="networkidle")
        # 안전망: renderMathInElement가 아직 안 끝났을 수 있으니 명시적으로 한 번 더 호출.
        # KaTeX auto-render는 이미 처리한 노드를 건너뛰므로 중복 호출은 무해.
        await page.evaluate(
            """
            () => new Promise((resolve, reject) => {
                if (typeof renderMathInElement === 'function') {
                    try { renderMathInElement(document.body); } catch (e) {}
                    resolve();
                } else {
                    let tries = 0;
                    const t = setInterval(() => {
                        tries += 1;
                        if (typeof renderMathInElement === 'function') {
                            clearInterval(t);
                            try { renderMathInElement(document.body); } catch (e) {}
                            resolve();
                        } else if (tries > 100) {
                            clearInterval(t);
                            resolve();  // 수식이 없는 문서일 수도 있음 — 그냥 진행
                        }
                    }, 50);
                }
            })
            """
        )
        await page.emulate_media(media="print")
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
            print_background=True,
            prefer_css_page_size=True,
        )
        await context.close()
        await browser.close()


def render_html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    """동기 래퍼."""
    asyncio.run(_html_to_pdf_async(html_path, pdf_path))


def pdf_to_pngs(pdf_path: Path, render_dir: Path, dpi: int = 200) -> list[Path]:
    """PyMuPDF로 PDF → 페이지별 PNG."""
    import fitz  # type: ignore

    render_dir.mkdir(parents=True, exist_ok=True)
    out_paths = []
    doc = fitz.open(str(pdf_path))
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out = render_dir / f"page_{i}.png"
        pix.save(str(out))
        out_paths.append(out)
    doc.close()
    return out_paths


def main():
    parser = argparse.ArgumentParser(description="Render analysis.md to HTML + PNGs")
    parser.add_argument("md", help="Path to analysis.md")
    parser.add_argument(
        "--output", required=True, help="Output directory (paper_dir)"
    )
    parser.add_argument(
        "--dpi", type=int, default=200,
        help="PNG render DPI. 150은 한글이 흐려져서 Render Evaluator가 raw LaTeX로 "
             "오해하는 사고가 있었음. 200+ 권장.",
    )
    args = parser.parse_args()

    md_path = Path(args.md)
    out_dir = Path(args.output)
    base_dir = md_path.parent

    html_path = out_dir / "analysis.html"
    pdf_path = out_dir / "analysis.pdf"
    render_dir = out_dir / "render"

    if render_dir.exists():
        for f in render_dir.glob("page_*.png"):
            f.unlink()

    render_md_to_html(md_path, html_path, base_dir)
    render_html_to_pdf(html_path, pdf_path)
    pages = pdf_to_pngs(pdf_path, render_dir, dpi=args.dpi)

    import json

    print(
        json.dumps(
            {
                "html_path": str(html_path),
                "pdf_path": str(pdf_path),
                "render_dir": str(render_dir),
                "page_count": len(pages),
                "pages": [str(p) for p in pages],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
