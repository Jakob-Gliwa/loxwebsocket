#!/usr/bin/env python3
"""Raise the minimum version of runtime dependencies that have known vulnerabilities.

Why this exists: Dependabot only updates ``uv.lock`` (the lock file is not part
of the published package). Users of the library resolve dependencies from the
ranges in ``pyproject.toml`` (e.g. ``aiohttp>=3.14.1``). If the *lowest* allowed
version has a known vulnerability, users can still end up with it. The only way
to protect them is to raise the lower bound and publish a new release.

Sub-commands
------------
probe   Write a minimal project (runtime deps only, ``resolution = "lowest-direct"``)
        into a directory. ``uv lock && uv audit`` in that directory audits exactly
        the versions at the lower bounds.
raise   Read the ``uv audit --output-format json`` result and raise the lower bound
        of every vulnerable *direct* runtime dependency to the smallest fixed
        version; optionally bump the patch version.

Run with: ``uv run --no-project --python 3.12 --with packaging python <this file> ...``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

DEPS_BLOCK = re.compile(r"(?ms)^(dependencies\s*=\s*\[)(.*?)(^\])")
VERSION_LINE = re.compile(r'(?m)^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
LOWER_BOUND = re.compile(r">=\s*[0-9A-Za-z.!+_-]+")


def runtime_deps(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def cmd_probe(args: argparse.Namespace) -> int:
    deps = runtime_deps(Path(args.pyproject))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pyproject.toml").write_text(
        "[project]\n"
        'name = "lowest-direct-probe"\n'
        'version = "0"\n'
        'requires-python = ">=3.10"\n'
        f"dependencies = {json.dumps(deps)}\n"
        "\n[tool.uv]\n"
        'resolution = "lowest-direct"\n',
        encoding="utf-8",
    )
    return 0


def cmd_raise(args: argparse.Namespace) -> int:
    pyproject = Path(args.pyproject)
    text = pyproject.read_text(encoding="utf-8")
    deps = runtime_deps(pyproject)
    direct = {canonicalize_name(Requirement(d).name): d for d in deps}

    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    needed: dict[str, Version] = {}
    unfixable: list[str] = []
    advisories: dict[str, set[str]] = {}

    for vuln in audit.get("vulnerabilities", []):
        name = canonicalize_name(vuln["dependency"]["name"])
        if name not in direct:
            continue  # transitive: resolved to latest in lowest-direct mode
        current = Version(vuln["dependency"]["version"])
        fixes = [Version(v) for v in vuln.get("fix_versions", [])]
        fixes = [v for v in fixes if v > current]
        advisories.setdefault(name, set()).add(vuln.get("display_id", vuln["id"]))
        if not fixes:
            unfixable.append(f"{name} {current}: {vuln['id']}")
            continue
        fix = min(fixes)
        if name not in needed or fix > needed[name]:
            needed[name] = fix

    result: dict[str, object] = {
        "changed": False,
        "packages": {},
        "unfixable": unfixable,
        "new_version": None,
    }

    if not needed:
        print(json.dumps(result))
        return 0

    # Rewrite only inside the [project] dependencies = [...] block.
    match = DEPS_BLOCK.search(text)
    if not match:
        print("error: could not find dependencies block", file=sys.stderr)
        return 2
    block = match.group(2)
    summary_lines = []
    for name, fix in sorted(needed.items()):
        old = direct[name]
        if not LOWER_BOUND.search(old):
            print(f"error: {old!r} has no '>=' lower bound to raise", file=sys.stderr)
            return 2
        new = LOWER_BOUND.sub(f">={fix}", old, count=1)
        if block.count(f'"{old}"') != 1:
            print(f"error: could not uniquely locate {old!r}", file=sys.stderr)
            return 2
        block = block.replace(f'"{old}"', f'"{new}"')
        result["packages"][name] = str(fix)  # type: ignore[index]
        ids = ", ".join(sorted(advisories[name]))
        summary_lines.append(f"- `{old}` -> `{new}` ({ids})")
    text = text[: match.start(2)] + block + text[match.end(2) :]

    if args.bump:
        vm = VERSION_LINE.search(text)
        if not vm:
            print("error: could not find project version", file=sys.stderr)
            return 2
        major, minor, patch = (int(x) for x in vm.groups())
        new_version = f"{major}.{minor}.{patch + 1}"
        text = text[: vm.start()] + f'version = "{new_version}"' + text[vm.end() :]
        result["new_version"] = new_version

    pyproject.write_text(text, encoding="utf-8")
    result["changed"] = True

    if args.summary:
        body = (
            "Automatic security floor update.\n\n"
            "The lowest version allowed by `pyproject.toml` has known "
            "vulnerabilities (found via `uv audit` on a `lowest-direct` "
            "resolution). Raising the lower bound makes sure new installs of "
            "this library never resolve to a vulnerable version.\n\n"
            + "\n".join(summary_lines)
            + "\n"
        )
        if unfixable:
            body += "\nNo fixed version available yet (not changed):\n" + "\n".join(
                f"- {u}" for u in unfixable
            )
            body += "\n"
        Path(args.summary).write_text(body, encoding="utf-8")

    print(json.dumps(result))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--pyproject", default="pyproject.toml")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_probe)

    r = sub.add_parser("raise")
    r.add_argument("--pyproject", default="pyproject.toml")
    r.add_argument("--audit", required=True)
    r.add_argument("--bump", action="store_true", help="bump patch version")
    r.add_argument("--summary", help="write PR body markdown here")
    r.set_defaults(func=cmd_raise)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
