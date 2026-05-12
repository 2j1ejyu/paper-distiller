#!/usr/bin/env python3
"""
update_format_prompt.py — format-prompt.md 백업 + 사용자 피드백 기록.

이 스크립트는 *형식 프롬프트의 의미적 수정은 하지 않는다*.
역할:
  1) prompts/format-prompt.md를 prompts/format-prompt.history/v{n}.md로 백업
  2) 사용자 피드백을 prompts/format-prompt.history/v{n}.feedback.txt로 기록
  3) 다음 버전 번호를 표준출력으로 반환 (메인 Claude가 Edit 도구로 수정 후 사용)

사용:
  python update_format_prompt.py --feedback "다음부터는 한계 섹션 더 자세히" --harness <harness-root>
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def next_version(history_dir: Path) -> int:
    """history/ 안의 v{N}.md 중 최대 N + 1 반환. 없으면 1."""
    max_n = 0
    for f in history_dir.glob("v*.md"):
        m = re.match(r"v(\d+)\.md$", f.name)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n + 1


def main():
    parser = argparse.ArgumentParser(
        description="Backup format-prompt.md and record user feedback before LLM edits it"
    )
    parser.add_argument(
        "--feedback", required=True, help="User feedback text in natural language"
    )
    parser.add_argument(
        "--harness", required=True, help="Path to the harness project root"
    )
    args = parser.parse_args()

    harness = Path(args.harness)
    fmt_path = harness / "prompts" / "format-prompt.md"
    history_dir = harness / "prompts" / "format-prompt.history"

    if not fmt_path.exists():
        print(f"Error: {fmt_path} not found", file=sys.stderr)
        sys.exit(1)

    history_dir.mkdir(parents=True, exist_ok=True)

    n = next_version(history_dir)
    backup_md = history_dir / f"v{n}.md"
    feedback_file = history_dir / f"v{n}.feedback.txt"

    shutil.copy(fmt_path, backup_md)

    timestamp = datetime.now().isoformat(timespec="seconds")
    feedback_file.write_text(
        f"# Feedback recorded at {timestamp}\n\n"
        f"# This feedback motivated the change from v{n} → v{n + 1} of format-prompt.md.\n"
        f"# The actual edit was performed by the LLM after this backup.\n\n"
        f"{args.feedback}\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "backed_up_to": str(backup_md),
                "feedback_recorded_to": str(feedback_file),
                "current_format_prompt": str(fmt_path),
                "next_version": n + 1,
                "instruction": (
                    "Now use the Edit tool to modify format-prompt.md based on the feedback. "
                    "The previous version is preserved in the history file above."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
