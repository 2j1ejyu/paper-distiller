#!/usr/bin/env bash
# paper-harness — first-run setup
# Creates .venv inside this directory, installs Python deps into it, and
# checks for required system binaries. Idempotent.

set -e
cd "$(dirname "$0")"

HARNESS_DIR="$(pwd)"
VENV_DIR="${HARNESS_DIR}/.venv"

# 1. venv 생성 (없을 때만)
if [ ! -f "${VENV_DIR}/bin/python" ]; then
  echo "[setup] Creating venv at ${VENV_DIR}..."
  python3 -m venv "${VENV_DIR}"
else
  echo "[setup] venv already exists at ${VENV_DIR}"
fi

# 2. pip 업그레이드 + 요구사항 설치
echo "[setup] Upgrading pip..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[setup] Installing requirements..."
"${VENV_DIR}/bin/pip" install --quiet -r "${HARNESS_DIR}/requirements.txt"

# Playwright는 pip 설치 후 별도로 Chromium 바이너리를 받아야 한다.
# 이미 받아둔 적이 있으면 빠르게 no-op.
echo "[setup] Installing Playwright Chromium..."
"${VENV_DIR}/bin/python" -m playwright install chromium

# 3. 시스템 의존성 검사 (정보성 — 실패해도 setup 자체는 통과)
echo ""
echo "[setup] Checking system dependencies..."

if ! command -v pandoc >/dev/null 2>&1; then
  echo "  ⚠️  pandoc not found"
  echo "     macOS: brew install pandoc"
  echo "     Linux: apt install pandoc  (or dnf install pandoc)"
else
  echo "  ✓ pandoc: $(pandoc --version | head -1)"
fi

# Playwright Chromium은 위에서 받았지만, 시스템 라이브러리(libnss3 등)가
# 없으면 실제 실행 시 죽는다. 한 번 헤드리스 launch만 해봐서 검증.
echo -n "  Playwright Chromium launch test... "
if "${VENV_DIR}/bin/python" -c "
import asyncio
from playwright.async_api import async_playwright
async def t():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await b.close()
asyncio.run(t())
" 2>/dev/null; then
  echo "✓"
else
  echo "✗"
  echo "     Chromium failed to launch. Missing system libs?"
  echo "     Linux: ${VENV_DIR}/bin/python -m playwright install-deps chromium"
  echo "     (또는 직접: apt install libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2)"
fi

echo ""
echo "[setup] Done."
echo "[setup] .venv ready at ${VENV_DIR}"
echo "[setup] Run scripts via: ${VENV_DIR}/bin/python scripts/<script>.py ..."
