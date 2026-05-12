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

# WeasyPrint는 cairo/pango/gdk-pixbuf 시스템 라이브러리가 있어야 import 가능
echo -n "  WeasyPrint import test... "
if "${VENV_DIR}/bin/python" -c "import weasyprint" 2>/dev/null; then
  echo "✓"
else
  echo "✗"
  echo "     WeasyPrint failed to import. Install system libs:"
  echo "     macOS: brew install cairo pango gdk-pixbuf libffi"
  echo "     Linux: apt install libpango-1.0-0 libpangoft2-1.0-0"
fi

echo ""
echo "[setup] Done."
echo "[setup] .venv ready at ${VENV_DIR}"
echo "[setup] Run scripts via: ${VENV_DIR}/bin/python scripts/<script>.py ..."
