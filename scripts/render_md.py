#!/usr/bin/env python3
"""
render_md.py — analysis.md를 HTML로 렌더하고 페이지별 PNG 스크린샷 생성.

파이프라인:
  .md  --pandoc(--mathml)-->  analysis.html  (수식은 서버사이드 MathML로 변환됨)
  .html --weasyprint-->  analysis.pdf       (WeasyPrint 60+ 가 MathML 렌더)
  .pdf --PyMuPDF-->  render/page_1.png, page_2.png, ...

산출물:
  {output}/analysis.html
  {output}/analysis.pdf       (중간물)
  {output}/render/page_N.png

Render Evaluator가 page_N.png를 멀티모달로 본다.

note: WeasyPrint가 cairo/pango 등 시스템 라이브러리에 의존한다. macOS Apple Silicon에서
`/opt/homebrew/lib` 경로가 dyld 검색 경로에 없으면 import 시점에 OSError가 난다.
이 스크립트는 import 전에 직접 환경변수를 점검해서, 필요하면 자기 자신을
DYLD_FALLBACK_LIBRARY_PATH가 설정된 새 프로세스로 exec한다 (오케스트레이터가
env를 안 깔아둬도 동작).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _ensure_dyld_for_weasyprint() -> None:
    """macOS에서 cairo/pango/glib 시스템 라이브러리를 찾을 수 있게 dyld 경로 보강.
    이미 적용된 경우 또는 라이브러리가 없는 경우(linux 등)는 skip."""
    if sys.platform != "darwin":
        return
    candidates = ["/opt/homebrew/lib", "/usr/local/lib"]
    needed = [c for c in candidates if Path(c, "libgobject-2.0.0.dylib").exists()
              or Path(c, "libgobject-2.0.dylib").exists()]
    if not needed:
        return  # 라이브러리 자체가 없음 → 그대로 진행, 사용자에게 brew 설치 안내
    current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if all(p in current.split(":") for p in needed):
        return  # 이미 설정되어 있음
    new_path = ":".join(needed + ([current] if current else ["/usr/lib"]))
    # 자식 프로세스로 다시 실행 (env 변수가 dlopen 시점에 적용되도록)
    new_env = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": new_path,
               "_RENDER_MD_REEXEC": "1"}
    if os.environ.get("_RENDER_MD_REEXEC") == "1":
        return  # 이미 한 번 재실행했음 — 무한 루프 방지
    os.execve(sys.executable, [sys.executable, *sys.argv], new_env)


_ensure_dyld_for_weasyprint()

CSS = """
@page { size: A4; margin: 2cm; }
body {
    font-family: -apple-system, "Helvetica Neue", "Noto Sans KR", sans-serif;
    line-height: 1.6;
    color: #222;
    max-width: 820px;
    margin: 0 auto;
    font-size: 11pt;
    box-sizing: border-box;
}
/* 브라우저로 열었을 때만 종이 느낌 (회색 배경 + 흰 본문 + 그림자).
   인쇄/PDF에는 적용 안 됨 — @page 마진이 이미 종이 여백을 잡고 있음. */
@media screen {
    html { background: #eee; }
    body {
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
/* pandoc --mathml은 MathML과 함께 <annotation encoding="application/x-tex">에 원본 LaTeX을
   넣는다. WeasyPrint는 이 annotation 텍스트도 같이 렌더해서 수식 옆에 raw \\command가
   나오는 사고가 생긴다. annotation은 보조용이니 시각 출력에서 숨긴다. */
annotation, semantics > annotation { display: none; }
math { font-family: "STIX Two Math", "Latin Modern Math", "Cambria Math", serif; }
"""


def have_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def render_md_to_html(md_path: Path, html_path: Path, base_dir: Path) -> None:
    """pandoc으로 .md → 단독 HTML. CSS는 <style>로 inline해 외부 의존성 제거."""
    if not have_command("pandoc"):
        raise RuntimeError(
            "pandoc not found. Install with: brew install pandoc (or apt/dnf install pandoc)"
        )

    # <style> 블록을 head에 직접 inject (pandoc -H). 이전엔 --css <abs path>로
    # 외부 .css를 링크했는데, 절대경로 link라 HTML을 다른 곳에서 열거나 file://
    # stylesheet 로드가 막힌 환경(원격 뷰어 등)에서 스타일이 통째로 무시되는
    # 사고가 있었음. inline으로 박으면 어디서 열어도 같은 모양.
    head_path = html_path.parent / "_render_head.html"
    head_path.write_text(f"<style>\n{CSS}\n</style>\n")

    cmd = [
        "pandoc",
        str(md_path),
        "-o",
        str(html_path),
        "--standalone",
        "--mathml",  # 서버사이드 MathML — WeasyPrint가 직접 렌더 (JS 불필요)
        "-H",
        str(head_path),
        "--resource-path",
        str(base_dir),  # 상대 이미지 경로 해석 기준
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"pandoc failed: {result.stderr}")
    finally:
        head_path.unlink(missing_ok=True)


def render_html_to_pdf(html_path: Path, pdf_path: Path, base_dir: Path) -> None:
    """WeasyPrint로 HTML → PDF."""
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "weasyprint not installed. Install with: "
            "pip install weasyprint --break-system-packages"
        ) from e

    HTML(filename=str(html_path), base_url=str(base_dir)).write_pdf(str(pdf_path))


def pdf_to_pngs(pdf_path: Path, render_dir: Path, dpi: int = 150) -> list[Path]:
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
    base_dir = md_path.parent  # analysis.md가 있는 폴더 = 이미지 상대 경로 기준

    html_path = out_dir / "analysis.html"
    pdf_path = out_dir / "analysis.pdf"
    render_dir = out_dir / "render"

    # 기존 render 정리
    if render_dir.exists():
        for f in render_dir.glob("page_*.png"):
            f.unlink()

    render_md_to_html(md_path, html_path, base_dir)
    render_html_to_pdf(html_path, pdf_path, base_dir)
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
