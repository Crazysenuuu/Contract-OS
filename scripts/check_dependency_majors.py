#!/usr/bin/env python3
"""Alert on new releases beyond the upper bounds in requirements.txt.

The dependency audit caps risky packages in backend/requirements.txt
(fastapi<1, pydantic<3, celery<6, sqlalchemy<2.2, alembic<1.21 — each cap
carries a rationale comment). A cap only works if someone notices when the
next major actually ships: this script compares every upper bound against
PyPI's latest version and reports each bound that is now exceeded.

Run weekly by .github/workflows/dependency-major-alerts.yml, which turns
alerts into a tracking issue.

Stdlib only (urllib + json): the CI job runs on a bare runner and must not
install the 135-package lock just to compare versions.

Exact pins (==) are skipped on purpose — boto3/opentelemetry are pinned for
documented compatibility reasons (see requirements.txt) and "a newer patch
exists" is not an alert.

Pre-releases (6.0.0rc1, 1.0b2) never trigger alerts: a stable major has not
landed yet, and rc churn would make the weekly issue noisy.

Exit codes: 0 = no alerts (or only skipped/unreachable packages),
1 = at least one bound exceeded (with --fail-on-alert),
2 = usage or file error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"

PYPI_TIMEOUT_S = 15
PYPI_RETRIES = 2

# name -> canonical PyPI name (PEP 503).
def pypi_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_version(v: str) -> tuple[tuple[int, ...], bool]:
    """('2.13.5') -> ((2, 13, 5), False); ('6.0.0rc1') -> ((6, 0, 0), True)."""
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", v.strip())
    if not m:
        return (0,), False
    nums = tuple(int(p) for p in m.group(1).split("."))
    return nums, bool(m.group(2))


def cmp_versions(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Pad with zeros and compare: (2,13,5) vs (2,2) -> 1."""
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def parse_upper_bound(specifiers: str) -> tuple[tuple[int, ...], bool] | None:
    """Return the tightest '<' / '<=' bound as (version_tuple, inclusive).

    '==' pins and '~='/'reserved operators are ignored deliberately.
    """
    bounds: list[tuple[tuple[int, ...], bool]] = []
    for tok in re.findall(r"(<=|>=|==|!=|~=|<|>)\s*([^\s,;]+)", specifiers):
        op, ver = tok
        if op not in ("<", "<="):
            continue
        nums, _pre = parse_version(ver.split(";")[0])
        bounds.append((nums, op == "<="))
    if not bounds:
        return None
    # Tightest = smallest bound; on a tie, '<' (exclusive) wins over '<='.
    return min(bounds, key=lambda b: (b[0], not b[1]))


def parse_requirements(path: Path) -> list[tuple[str, str, str]]:
    """Yield (canonical_name, upper_bound_spec, raw_line) for bounded deps."""
    entries: list[tuple[str, str, str]] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):  # options, -r, -e, --index-url
            continue
        line = line.split(";", 1)[0].strip()  # drop environment markers
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$", line)
        if not m:
            continue
        name, _extras, spec = m.groups()
        bound = parse_upper_bound(spec)
        if bound is None:
            continue
        entries.append((pypi_name(name), spec, raw.strip()))
    return entries


def fetch_latest_version(name: str) -> str | None:
    url = f"https://pypi.org/pypi/{name}/json"
    for attempt in range(PYPI_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=PYPI_TIMEOUT_S) as resp:
                data = json.load(resp)
            version = (data.get("info") or {}).get("version")
            if version:
                return version
        except Exception:
            if attempt == PYPI_RETRIES - 1:
                return None
            time.sleep(2)
    return None


def check(requirements_path: Path) -> tuple[list[str], int]:
    """Return (report_lines, alert_count). Network gaps are warnings, not alerts."""
    lines: list[str] = []
    alerts = 0
    entries = parse_requirements(requirements_path)
    lines.append(f"requirements file: {requirements_path}")
    lines.append(f"bounded dependencies checked against PyPI: {len(entries)}")
    for name, spec, raw in entries:
        latest = fetch_latest_version(name)
        if latest is None:
            lines.append(f"WARN  {name}: PyPI unreachable or unknown project — skipped ({raw})")
            continue
        latest_nums, is_pre = parse_version(latest)
        bound_nums, inclusive = parse_upper_bound(spec)
        assert bound_nums is not None  # parse_requirements guarantees it
        if is_pre:
            lines.append(f"ok    {name}: latest {latest} is a pre-release — bound {spec} not challenged")
            continue
        c = cmp_versions(latest_nums, bound_nums)
        exceeded = c > 0 or (c == 0 and not inclusive)
        if exceeded:
            alerts += 1
            lines.append(
                f"ALERT {name}: latest {latest} exceeds bound {spec} — "
                f"update the cap after testing ({raw})"
            )
        else:
            lines.append(f"ok    {name}: latest {latest} within bound {spec}")
    lines.append(f"result: {alerts} alert(s)")
    return lines, alerts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "requirements",
        nargs="?",
        type=Path,
        default=DEFAULT_REQUIREMENTS,
        help=f"path to requirements file (default: {DEFAULT_REQUIREMENTS})",
    )
    parser.add_argument(
        "--fail-on-alert",
        action="store_true",
        help="exit 1 when at least one bound is exceeded (used by CI)",
    )
    args = parser.parse_args()

    if not args.requirements.is_file():
        print(f"error: requirements file not found: {args.requirements}", file=sys.stderr)
        return 2

    lines, alerts = check(args.requirements)
    print("\n".join(lines))
    if alerts and args.fail_on_alert:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
