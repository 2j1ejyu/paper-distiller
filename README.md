# paper-distiller

A personal multi-agent harness that takes a single paper and produces one `.md` document formatted to your spec.

## Concept

- **You** supply the paper link or path (no automatic discovery).
- The **Writer subagent** reads `prompts/format-prompt.md` and writes `analysis.md` in that format, picking and embedding key figures itself.
- The **Content Evaluator subagent** scores format compliance and factual accuracy by comparing the original PDF against `analysis.md` in a fresh context.
- The **Render Evaluator subagent** scores visual integrity from screenshots of the rendered HTML.
- Loops automatically until all three PASS (max 3 iterations).
- If you don't like the result, edit `prompts/format-prompt.md` — the new format applies from the next run.

## Layout

```
<project-root>/
├── CLAUDE.md
├── SKILL.md                       step-by-step workflow the main Claude follows
├── setup.sh
├── requirements.txt
├── prompts/
│   ├── format-prompt.md           ★ the format spec you edit and evolve
│   ├── writer-prompt.md
│   ├── content-eval-prompt.md
│   ├── render-eval-prompt.md
│   └── format-prompt.history/     automatic backups of format-prompt.md
├── scripts/
│   ├── fetch_paper.py             organizes a local PDF into a slug folder
│   ├── resolve_doi.py             resolves a DOI to a PDF URL via CrossRef
│   ├── process_pdf.py             single docling pass: text, sections, metadata, figures
│   ├── render_md.py               .md → HTML → PDF → per-page PNG
│   ├── finalize_paper.py          renames the output folder to the paper-title slug on PASS
│   └── update_format_prompt.py    backs up and saves a new prompt
└── output/{paper-slug}/           per-paper artifacts (gitignored)
    ├── paper.pdf
    ├── parsed.json
    ├── figures/all/figure_*.png
    ├── figures/figures.json
    ├── analysis.md                ← final artifact
    ├── analysis.html
    ├── analysis.pdf
    ├── render/page_*.png
    ├── attempts/v{n}.md, v{n}.eval.json
    └── final.eval.json
```

## Usage

### 1. Setup (one time)

Requires Python 3.10+ and pandoc. The workflow aborts immediately if `.venv` is missing or dependencies fail to import, so run this before requesting any analysis.

```bash
bash setup.sh
```

System dependencies you need to install yourself:

```bash
# macOS
brew install pandoc

# Debian/Ubuntu
sudo apt install pandoc
```

HTML → PDF rendering uses Playwright Chromium. On Linux, if the system libraries needed by Chromium (e.g. libnss3) are missing, also run `python -m playwright install-deps chromium`.

### 2. Analyze a paper

With the project root as your cwd, ask the main Claude in natural language:

```
Analyze this paper: https://arxiv.org/abs/1706.03762
```

### 3. Format feedback

Tell Claude something like "from now on, format it like this …" and it will back up `prompts/format-prompt.md` and apply your feedback. You can also edit the file directly.

## How it works

The main Claude orchestrates the run by following `SKILL.md` step by step:

1. **Fetch.** The input (arXiv URL/ID, DOI, generic PDF URL, or local path) is resolved to a local PDF and placed under `output/<temp-slug>/paper.pdf`.
2. **Process.** `process_pdf.py` runs a single docling pass that emits `parsed.json` (metadata, full text, sections, captions) and per-figure PNGs plus `figures/figures.json`. Multi-panel figures sharing one number are split into sub-panels (`figure_N_a.png`, …); pictures docling can't link to a caption land in an `unmatched` list for the Writer to reconcile.
3. **Write → render → evaluate (loop, up to 3 attempts).** All three subagents share the same `subagent_type=general-purpose`; their roles come entirely from the system prompt file bundled into each call.
   - The **Writer** subagent runs with `prompts/writer-prompt.md` as its system role, with `prompts/format-prompt.md` attached inline and paths to `parsed.json` and `figures.json` to Read. It writes `analysis.md` with embedded figures.
   - `render_md.py` turns the `.md` into `analysis.html` (pandoc + KaTeX), then into `analysis.pdf`, then into per-page PNGs under `render/`.
   - The **Content Evaluator** (system role: `prompts/content-eval-prompt.md`, fresh context) compares `analysis.md` against `parsed.json` for format compliance and factual accuracy.
   - The **Render Evaluator** (system role: `prompts/render-eval-prompt.md`, fresh context) inspects the page PNGs for visual breakage.
   - Both evaluators run in parallel. Each attempt's `.md` and merged eval JSON are saved under `attempts/v{n}.*`. If either evaluator FAILs, the Writer is re-invoked with the feedback attached.
4. **Finalize.** When both evaluators PASS, `finalize_paper.py` renames the output directory from the temp slug to a paper-title slug. On final FAIL, the folder is left under the temp slug with the unresolved issues reported back to you.
5. **Format feedback (out of loop).** Any "next time, format it like this…" message triggers `update_format_prompt.py`, which backs up `format-prompt.md` to `format-prompt.history/` before Claude edits it in place.
