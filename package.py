#!/usr/bin/env python3
"""Build an AnkiWeb-ready .ankiaddon package for AnkiTube.

See https://addon-docs.ankiweb.net/sharing.html

Usage:
    python package.py              # write dist/AnkiTube.ankiaddon
    python package.py --check      # verify package contents without writing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = ADDON_DIR / "manifest.json"
OUTPUT_DIR = ADDON_DIR / "dist"

# Paths relative to the add-on root that must appear in the .ankiaddon zip.
REQUIRED_PATHS = (
    "__init__.py",
    "manifest.json",
    "config.json",
    "web/player.html",
)

# Top-level names and patterns kept out of published packages.
EXCLUDE_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".github",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "dist",
        ".DS_Store",
        "meta.json",
        ".gitignore",
        "README.md",
        "package.py",
    }
)
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".ankiaddon"})


def _load_manifest() -> dict:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _save_manifest(manifest: dict) -> None:
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=4)
        handle.write("\n")


def _should_exclude(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return any(part in EXCLUDE_NAMES for part in path.parts)


def collect_package_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ADDON_DIR.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ADDON_DIR)
        if _should_exclude(relative):
            continue
        files.append(relative)
    return files


def update_manifest_mod(manifest: dict) -> int:
    mod = int(time.time())
    manifest["mod"] = mod
    _save_manifest(manifest)
    return mod


def build_package(output_path: Path, *, update_mod: bool = True) -> list[str]:
    manifest = _load_manifest()
    if update_mod:
        update_manifest_mod(manifest)

    files = collect_package_files()
    missing = [name for name in REQUIRED_PATHS if name not in {str(p) for p in files}]
    if missing:
        raise SystemExit(f"Package is missing required files: {', '.join(missing)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(ADDON_DIR / relative, arcname=str(relative))

    return [str(path) for path in files]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="list package contents and validate required files without writing",
    )
    parser.add_argument(
        "--no-update-mod",
        action="store_true",
        help="do not bump manifest.json mod before packaging",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=OUTPUT_DIR / "AnkiTube.ankiaddon",
        help=f"output .ankiaddon path (default: {OUTPUT_DIR / 'AnkiTube.ankiaddon'})",
    )
    args = parser.parse_args(argv)

    if args.check:
        files = collect_package_files()
        missing = [name for name in REQUIRED_PATHS if name not in {str(p) for p in files}]
        if missing:
            print(f"Missing required files: {', '.join(missing)}", file=sys.stderr)
            return 1
        print(f"Would package {len(files)} file(s):")
        for name in files:
            print(f"  {name}")
        return 0

    files = build_package(args.output, update_mod=not args.no_update_mod)
    manifest = _load_manifest()
    print(f"Wrote {args.output}")
    print(f"  files: {len(files)}")
    print(f"  mod:   {manifest.get('mod')}")
    print()
    print("Upload at https://ankiweb.net/shared/addons/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
