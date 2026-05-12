# Paper Harness

논문 1편을 받아 → 사용자가 정의한 형식에 맞게 정리된 단일 .md 문서를 만드는 개인용 멀티에이전트 하네스.

## 핵심 컨셉

- **사용자**가 논문 링크/경로를 준다 (자동 검색 X)
- **Writer 서브에이전트**가 `prompts/format-prompt.md`를 읽고 그 형식대로 `analysis.md`를 작성, 핵심 피규어도 본인이 골라서 임베드
- **Content Evaluator 서브에이전트**가 fresh context에서 원본 PDF ↔ analysis.md 대조해 형식 준수 + 사실 정확성 채점
- **Render Evaluator 서브에이전트**가 렌더된 HTML 스크린샷을 보고 시각 무결성 채점
- 셋 다 PASS할 때까지 자동 루프 (max 3회)
- 결과가 마음에 안 들면 → `prompts/format-prompt.md`를 수동/명령으로 수정 → 다음번부터 새 형식 적용

## 구조

프로젝트 루트의 디렉토리 이름은 자유 — 모든 경로는 이 루트 기준으로만 표시한다.

```
<project-root>/
├── CLAUDE.md                      ← cwd 자동 로드: 논문 입력 시 SKILL.md 따르기 안내
├── SKILL.md                       ← 메인 Claude가 따라가는 단계 정의
├── setup.sh                       ← .venv 만들고 의존성 설치 (idempotent)
├── requirements.txt
├── .venv/                         ← Python 가상환경 (setup.sh가 생성, gitignore)
├── prompts/
│   ├── format-prompt.md           ★ 직접 편집·진화시키는 형식 명세
│   ├── writer-prompt.md           Writer 서브에이전트 시스템 지침
│   ├── content-eval-prompt.md     Content Evaluator 시스템 지침
│   ├── render-eval-prompt.md      Render Evaluator 시스템 지침
│   └── format-prompt.history/     format-prompt.md의 자동 백업
├── scripts/
│   ├── fetch_paper.py             로컬 PDF → slug 폴더로 정리 (다운로드는 셸 curl이 함)
│   ├── resolve_doi.py             DOI → CrossRef로 PDF URL 해석 (셸이 그 URL을 curl)
│   ├── parse_pdf.py               텍스트·섹션·메타 추출 (figure는 extract_figures.py)
│   ├── extract_figures.py         docling 기반 figure 추출 → figures.json + PNG
│   ├── render_md.py               .md → HTML → PDF → 페이지별 PNG 스크린샷
│   ├── finalize_paper.py          PASS 시 output 디렉토리를 논문 제목 슬러그로 rename
│   └── update_format_prompt.py    백업하고 새 prompt 저장
└── output/{paper-slug}/           논문별 산출물 (PASS 시 제목 슬러그로 rename, gitignore)
    ├── paper.pdf
    ├── parsed.json
    ├── figures/all/figure_*.png
    ├── figures/figures.json
    ├── analysis.md                ← 최종 산출물
    ├── analysis.html              ← 렌더 결과 (CSS inline, 단독 열람 가능)
    ├── analysis.pdf               ← 렌더 중간물 (HTML→PDF, render PNG의 원본)
    ├── render/page_*.png          ← Render Evaluator가 보는 페이지 이미지
    ├── attempts/v{n}.md, v{n}.eval.json
    └── final.eval.json
```

## 사용법

### 1. 셋업 (필수, 최초 1회)

> 메인 Claude는 의존성을 자동 설치하지 않는다. `.venv`가 없거나 필수 패키지 import이 실패하면 워크플로가 즉시 에러를 내고 중단한다. **분석 요청 전에 반드시 직접 돌려야 한다.**

프로젝트 루트로 이동해서:

```bash
bash setup.sh
```

`setup.sh`가 하는 일:
- `.venv/`를 프로젝트 루트 안에 생성 (시스템 Python에 패키지 설치 안 함)
- `requirements.txt`의 Python 패키지를 `.venv` 안에 설치
- 시스템 바이너리(`pandoc`, cairo/pango)가 있는지 검사 — 없으면 설치 안내만 출력

별도로 깔아둬야 하는 시스템 의존성:

```bash
# macOS
brew install pandoc

# Debian/Ubuntu
sudo apt install pandoc
```

> HTML → PDF 렌더는 Playwright Chromium이 담당. `setup.sh`가 `playwright install chromium`까지 알아서 돌리지만, Linux에서 Chromium 실행에 필요한 시스템 라이브러리(libnss3 등)가 없으면 `python -m playwright install-deps chromium`을 추가로 실행해야 한다.

### 2. 논문 분석

프로젝트 루트를 cwd로 둔 채 메인 Claude에게 자연어로 요청:

```
이 논문 분석해줘: https://arxiv.org/abs/1706.03762
```

cwd 자동 로드되는 `CLAUDE.md`가 메인 Claude를 `SKILL.md` 워크플로로 안내하고, 거기 정의된 단계대로 실행한다 (`.venv/bin/python`만 써서 시스템 Python은 안 건드림).

### 3. 형식 피드백

분석 결과를 보고 "다음부터는 ~이렇게 정리해줘" 라고 말하면, 메인 Claude가 `prompts/format-prompt.md`를 백업하고 피드백 반영해 갱신.

직접 편집해도 됨.

## 의존성

- **시스템**: Python 3.10+, pandoc
- **`.venv` 안**: PyMuPDF, playwright (+Chromium), docling (`setup.sh`가 자동 설치)

## 설계 의도

- **분리된 평가자**: Writer가 자기 글을 합리화하지 않도록, 평가는 fresh context의 별도 서브에이전트
- **두 종류 평가자**: 텍스트 사실/형식 평가와 시각적 깨짐은 입력 매체가 달라서 분리
- **버전 관리되는 형식 명세**: `format-prompt.md`가 진화하므로 history 자동 백업
- **시도 기록 보존**: `attempts/`에 각 사이클의 .md와 평가 JSON을 남겨 디버깅 가능
