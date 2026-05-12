---
name: paper-harness
description: 사용자가 제공한 논문(arXiv URL · DOI · 일반 PDF URL · 로컬 PDF 경로)을 분석해 단일 마크다운 문서로 정리. Writer · Content Evaluator · Render Evaluator 3개 서브에이전트로 작성+평가 루프(최대 3회)를 돌려, 형식 명세 준수·사실 정확성·시각 무결성 모두 통과하는 결과물을 생성. 사용자가 "이 논문 정리/분석/공부자료 만들어줘" + 논문 링크/경로를 제공할 때 발동.
allowed-tools: Bash, Write, Edit, Read, Agent
---

# Paper Harness — Orchestrator Instructions

이 문서는 메인 Claude(오케스트레이터)가 사용자가 논문 분석을 요청했을 때 따라가는 단계별 지침이다.

---

## Trigger

사용자가 다음 중 하나를 제공하면 이 워크플로 발동:
- arXiv URL (`https://arxiv.org/abs/...` 또는 `/pdf/...`)
- DOI (`10.xxxx/...`)
- 일반 PDF URL
- 로컬 PDF 경로

요청 표현 예시: "이 논문 정리해줘", "분석해줘", "공부 자료 만들어줘".

---

## 변수

워크플로 전반에서 다음 경로를 사용한다 (`HARNESS_DIR`은 이 SKILL.md 파일이 있는 폴더):

- `HARNESS_DIR` — 이 SKILL.md 파일이 있는 폴더(프로젝트 루트)의 절대 경로
- `PYTHON` — `${HARNESS_DIR}/.venv/bin/python` (시스템 Python 안 씀)
- `INPUT` — 사용자가 준 URL/경로 원본
- `SLUG` — fetch_paper.py가 결정한 slug (논문 폴더 이름)
- `PAPER_DIR` — `${HARNESS_DIR}/output/${SLUG}/`
- `MAX_ATTEMPTS` — 3 (변경하려면 여기만 고치면 됨)

---

## Step 1: 논문 확보

다운로드는 셸 `curl`이 처리하고, Python은 PDF에서 slug를 뽑아 폴더로 정리하는 일만 한다.

```bash
TMP_PDF="${HARNESS_DIR}/output/_tmp_fetch.pdf"
mkdir -p "$(dirname "$TMP_PDF")"

# 1) 입력 분기 → TMP_PDF에 PDF 확보
if [[ -f "$INPUT" && "${INPUT,,}" == *.pdf ]]; then
  # 로컬 PDF
  cp "$INPUT" "$TMP_PDF"
elif [[ "$INPUT" =~ ^10\.[0-9]{4,9}/.+ ]]; then
  # DOI: CrossRef로 PDF URL 해석 후 curl
  URL=$("${PYTHON}" "${HARNESS_DIR}/scripts/resolve_doi.py" "$INPUT") || exit 1
  curl -fL --max-time 60 -A "paper-harness/1.0" -o "$TMP_PDF" "$URL"
elif [[ "$INPUT" =~ ^[0-9]{4}\.[0-9]{4,5}(v[0-9]+)?$ ]]; then
  # arXiv ID
  curl -fL --max-time 60 -A "paper-harness/1.0" \
    -o "$TMP_PDF" "https://arxiv.org/pdf/${INPUT}.pdf"
elif [[ "$INPUT" =~ ^https?://arxiv\.org/abs/(.+)$ ]]; then
  # arXiv abs URL → pdf URL
  ID="${BASH_REMATCH[1]%/}"
  curl -fL --max-time 60 -A "paper-harness/1.0" \
    -o "$TMP_PDF" "https://arxiv.org/pdf/${ID}.pdf"
elif [[ "$INPUT" =~ ^https?://arxiv\.org/pdf/ ]]; then
  URL="$INPUT"
  [[ "$URL" != *.pdf ]] && URL="${URL}.pdf"
  curl -fL --max-time 60 -A "paper-harness/1.0" -o "$TMP_PDF" "$URL"
elif [[ "$INPUT" =~ ^https?:// ]]; then
  # 일반 PDF URL
  curl -fL --max-time 60 -A "paper-harness/1.0" -o "$TMP_PDF" "$INPUT"
else
  echo "Error: unrecognized input: $INPUT" >&2
  exit 1
fi

# 2) PDF 검증 + slug 폴더로 정리 → JSON 출력
file "$TMP_PDF" | grep -q 'PDF document' || { echo "Error: not a PDF" >&2; exit 1; }
"${PYTHON}" "${HARNESS_DIR}/scripts/fetch_paper.py" "$TMP_PDF" --output "${HARNESS_DIR}/output"
```

`fetch_paper.py`의 표준출력 JSON `{"slug": "...", "paper_path": "...", "paper_dir": "..."}`을 받아 `SLUG`, `PAPER_DIR` 변수로 저장.

실패 시 (curl 실패, PDF 검증 실패 등): 에러 메시지 사용자에게 보여주고 중단.

---

## Step 2: 파싱 + 피규어 추출 (단일 docling 패스)

텍스트와 figure를 한 번의 docling 패스로 같이 뽑는다. PDF를 두 번 읽지 않는다.

```bash
"${PYTHON}" "${HARNESS_DIR}/scripts/process_pdf.py" "${PAPER_DIR}/paper.pdf" --output "${PAPER_DIR}"
```

생성물:
- `${PAPER_DIR}/parsed.json` — metadata, full_text, sections, captions
- `${PAPER_DIR}/figures/all/figure_{N}.png` (또는 multi-panel은 `figure_{N}_{a,b,...}.png`)
- `${PAPER_DIR}/figures/figures.json` — `{extracted, skipped}` 메타 (Writer가 읽음)

내부 처리 요약:
- layout 모델은 `DOCLING_LAYOUT_EGRET_XLARGE` (멀티 패널 sub-figure 탐지 안정성)
- 캡션 매칭: docling이 picture에 link해준 캡션을 1차로 신뢰, 없으면 같은 page 안의
  `Figure N:` 시작 텍스트 중 geometric center가 가장 가까운 것을 fallback으로 연결
- 같은 figure number를 공유하는 picture가 ≥2개면 sub-panel로 저장 (top→bottom,
  left→right 순으로 a, b, c… suffix 부여). figures.json record의 `image_path` 필드를
  Writer가 그대로 사용한다.

stdout JSON `{"parsed_path": "...", "figures_json": "...", "extracted": N, "skipped": M, ...}`은
로깅용. 스크립트가 비정상 exit이면 메인 Claude는 작업을 중단한다 (parse 단계 실패는
fallback이 없다 — 텍스트 없으면 writer가 작동 못 함).

---

## Step 3: 분석 루프

`${PAPER_DIR}/attempts/` 디렉터리 생성. attempt 카운터 `n`을 1부터 시작.

루프 본문 (n = 1, 2, 3 ... MAX_ATTEMPTS까지):

### 3a. Writer 서브에이전트 호출

`Agent` 도구를 사용한다 (`subagent_type="general-purpose"`). 프롬프트는 다음과 같이 구성한다:

> **System role**: `prompts/writer-prompt.md`의 전체 내용
> 
> **Inputs**:
> - 형식 명세: `prompts/format-prompt.md`를 읽어서 첨부
> - 논문 텍스트: `${PAPER_DIR}/parsed.json`
> - 피규어 메타: `${PAPER_DIR}/figures/figures.json` (Step 2b의 extract_figures.py 산출물)
> - 피규어 디렉터리: `${PAPER_DIR}/figures/all/`
> - 출력 경로: `${PAPER_DIR}/analysis.md`
> - (n ≥ 2인 경우) 이전 시도의 평가 피드백: `${PAPER_DIR}/attempts/v{n-1}.eval.json` + 이전 시도의 .md (`${PAPER_DIR}/attempts/v{n-1}.md`)
>
> **Task**: format-prompt.md의 형식을 그대로 따라 analysis.md를 작성하라. 핵심 피규어는 `figures.json`의 `extracted`에서 골라 본문 흐름에 맞게 임베드 (`![](figures/all/figure_N.png)` 형태). `extracted`가 비어있으면 figure 임베드는 생략 — 가짜 경로 절대 적지 않기. 이전 평가 피드백이 있으면 지적된 부분만 수정.

Writer가 `analysis.md`를 직접 Write하도록 지시한다.

### 3b. 시도 백업

```bash
cp "${PAPER_DIR}/analysis.md" "${PAPER_DIR}/attempts/v${n}.md"
```

### 3c. HTML/PNG 렌더링

```bash
"${PYTHON}" "${HARNESS_DIR}/scripts/render_md.py" "${PAPER_DIR}/analysis.md" --output "${PAPER_DIR}"
```

생성물: `${PAPER_DIR}/analysis.html`, `${PAPER_DIR}/render/page_1.png`, `page_2.png`, ...

### 3d. 두 평가자 병렬 호출

**한 메시지에 두 개의 Agent 도구 호출**을 보내서 병렬 실행 (Cowork 가이드 따름).

#### Content Evaluator

> **System role**: `prompts/content-eval-prompt.md`의 전체 내용
> 
> **Inputs**:
> - 원본 논문: `${PAPER_DIR}/parsed.json` (텍스트 전문)
> - 작성된 글: `${PAPER_DIR}/analysis.md`
> - 형식 명세: `prompts/format-prompt.md`
> 
> **Output**: 다음 JSON을 `${PAPER_DIR}/attempts/v${n}.content-eval.json`으로 저장:
> ```json
> {
>   "format_compliance": {"verdict": "PASS|FAIL", "score": 0-10, "issues": [...]},
>   "factual_accuracy":  {"verdict": "PASS|FAIL", "score": 0-10, "errors": [...]},
>   "overall": "PASS|FAIL"
> }
> ```

#### Render Evaluator

> **System role**: `prompts/render-eval-prompt.md`의 전체 내용
> 
> **Inputs**:
> - 렌더된 페이지 이미지들: `${PAPER_DIR}/render/page_*.png` (서브에이전트가 Read로 이미지 직접 봄)
> - 형식 명세: `prompts/format-prompt.md` (참고용)
> 
> **Output**: 다음 JSON을 `${PAPER_DIR}/attempts/v${n}.render-eval.json`으로 저장:
> ```json
> {
>   "rendering_integrity": {"verdict": "PASS|FAIL", "score": 0-10, "issues": [...]},
>   "overall": "PASS|FAIL"
> }
> ```

### 3e. 평가 종합

두 JSON을 읽어서 합친 뒤 `${PAPER_DIR}/attempts/v${n}.eval.json`에 저장:

```json
{
  "attempt": n,
  "content": <v${n}.content-eval.json 전체>,
  "render":  <v${n}.render-eval.json 전체>,
  "overall": "PASS|FAIL"
}
```

`overall`은 두 평가의 `overall`이 모두 PASS일 때만 PASS.

### 3f. 분기

- **PASS** → 루프 탈출, Step 4로
- **FAIL & n < MAX_ATTEMPTS** → n += 1, 루프 다시 (3a부터, 이번엔 평가 피드백 첨부)
- **FAIL & n == MAX_ATTEMPTS** → 루프 종료, 마지막 시도를 결과로 채택하고 사용자에게 "최대 시도 도달, 평가에 미해결 이슈 있음" 알림

---

## Step 4: 결과 표시 + (PASS인 경우) 디렉토리 rename

`${PAPER_DIR}/attempts/v${final_n}.eval.json`을 `${PAPER_DIR}/final.eval.json`으로 복사.

**`overall == "PASS"`인 경우에만** 다음을 실행:

```bash
"${PYTHON}" "${HARNESS_DIR}/scripts/finalize_paper.py" \
  --paper-dir "${PAPER_DIR}"
```

stdout JSON `{"old_slug": "...", "new_slug": "...", "new_paper_dir": "..."}`을 받아
`PAPER_DIR`를 새 경로로 갱신. analysis.md 안의 figure 경로는 상대경로
(`figures/all/figure_N.png`)이므로 디렉토리 이동에 영향을 받지 않는다.

FAIL로 끝났으면 rename을 건너뛴다 — 미해결 이슈가 남은 결과물에 제목 이름을
붙이지 않는다.

사용자에게:
1. `${PAPER_DIR}/analysis.md` 경로 (PASS면 새 경로, FAIL이면 원본 경로)
2. 통과 시도 번호 (예: "2번째 시도에서 통과")
3. 평가 요약 (각 축의 score)
4. PASS면 추가로: 이전 slug → 새 slug 변경 알림
5. FAIL로 끝난 경우: 미해결 이슈 목록

---

## Step 5: (별도 명령) 형식 프롬프트 갱신

사용자가 결과를 보고 "다음부터는 ~이렇게 해줘" 라고 피드백하면:

```bash
"${PYTHON}" "${HARNESS_DIR}/scripts/update_format_prompt.py" \
  --feedback "<사용자 피드백 원문>" \
  --harness "${HARNESS_DIR}"
```

이 스크립트는 자동으로 백업하고 사용자 피드백 텍스트를 history에 기록한다. 실제 `format-prompt.md` 수정은 메인 Claude가 직접 Edit 도구로 수행:

1. `prompts/format-prompt.md` 현재 내용 읽음
2. 사용자 피드백을 어디에 어떻게 반영할지 판단
3. `Edit` 도구로 수정
4. 사용자에게 변경 사항 요약 표시

스크립트는 백업 + 피드백 기록만 하고, 의미적 수정은 LLM이 한다.

---

## 주의사항

- **Step 3a, 3d에서 서브에이전트 호출 시**, 메인 Claude는 그 응답을 그대로 사용자에게 보여주지 말고 결과 파일만 확인 후 다음 단계로 진행. 서브에이전트는 자기가 작성한 파일 경로만 짧게 보고함.
- **동일 메시지에 두 Agent 호출** = 병렬. 순차 호출하면 시간 낭비.
- **이전 시도 피드백을 Writer에게 줄 때**, 단순 dump 말고 "지적된 issue들을 어떻게 고칠지" 한 줄 가이드를 메인 Claude가 추가하면 수렴 빠름.
- **첫 attempt에서 PASS 가능성**도 충분하므로 무조건 3회 돌리지 않는다.
