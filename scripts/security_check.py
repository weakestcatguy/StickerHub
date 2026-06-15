#!/usr/bin/env python3
"""Run before deploy: scan for hardcoded secrets and known dependency vulnerabilities."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app", "scripts")
IGNORE_FILES = {".env", ".env.example"}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"sk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"postgresql://[^\s'\"]+:[^\s'\"]+@"),
]


def scan_for_secrets() -> list[str]:
    findings: list[str] = []
    for directory in SCAN_DIRS:
        base = ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".html", ".js", ".toml", ".json"}:
                continue
            if path.name in IGNORE_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for index, line in enumerate(text.splitlines(), start=1):
                for pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"{path.relative_to(ROOT)}:{index}: possible hardcoded secret")
    return findings


def run_pip_audit() -> tuple[int, str]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pip-audit", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return 1, completed.stderr or "Failed to install pip-audit."

        audit = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", str(ROOT / "requirements.txt")],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
        )
        return audit.returncode, audit.stdout + audit.stderr
    except OSError as exc:
        return 1, str(exc)


def main() -> int:
    print("=== Secret scan ===")
    secrets = scan_for_secrets()
    if secrets:
        print("Potential hardcoded secrets found:")
        for item in secrets:
            print(f"  - {item}")
    else:
        print("No obvious hardcoded secrets in application code.")

    print("\n=== Dependency audit (pip-audit) ===")
    code, output = run_pip_audit()
    print(output.strip() or "(no output)")
    if code != 0:
        print("Dependency audit reported issues or could not run.")

    return 1 if secrets else code


if __name__ == "__main__":
    raise SystemExit(main())
