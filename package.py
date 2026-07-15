#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build an AnkiWeb-ready .ankiaddon package for AnkiTube.

See https://addon-docs.ankiweb.net/sharing.html

Usage:
    python package.py              # write dist/AnkiTube.ankiaddon
    python package.py --check      # verify package contents without writing
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import zipfile
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = ADDON_DIR / "manifest.json"
ADDON_JSON_PATH = ADDON_DIR / "addon.json"
VERSION_PATH = ADDON_DIR / "_version.py"
OUTPUT_DIR = ADDON_DIR / "dist"

REQUIRED_PATHS = (
    "__init__.py",
    "manifest.json",
    "config.json",
    "web/player.html",
)

REQUIRED_ADDON_JSON_KEYS = (
    "display_name",
    "module_name",
    "author",
    "homepage",
    "min_anki_version",
)

EXCLUDE_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".github",
        ".idea",
        ".vscode",
        ".venv",
        ".pytest_cache",
        "venv",
        "env",
        "dist",
        ".DS_Store",
        "meta.json",
        ".gitignore",
        "README.md",
        "CONTRIBUTING.md",
        "addon.json",
        "config.md",
        "docs",
        "screenshots",
        "tests",
        "requirements-dev.txt",
        "requirements.txt",
        "pytest.ini",
        "package.py",
    }
)
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".ankiaddon"})


def _load_version() -> str:
    spec = importlib.util.spec_from_file_location("ankitube_version", VERSION_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load version from {VERSION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = getattr(module, "__version__", None)
    if not isinstance(version, str) or not version:
        raise SystemExit(f"Invalid __version__ in {VERSION_PATH}")
    return version


def _load_manifest() -> dict:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _save_manifest(manifest: dict) -> None:
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=4)
        handle.write("\n")


def _load_addon_json() -> dict:
    with ADDON_JSON_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_addon_json() -> list[str]:
    if not ADDON_JSON_PATH.is_file():
        return [f"Missing {ADDON_JSON_PATH.name}"]
    data = _load_addon_json()
    missing = [key for key in REQUIRED_ADDON_JSON_KEYS if not data.get(key)]
    if missing:
        return [f"addon.json missing keys: {', '.join(missing)}"]
    return []


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


def update_manifest_mod(manifest: dict, *, human_version: str) -> int:
    mod = int(time.time())
    manifest["mod"] = mod
    manifest["human_version"] = human_version
    _save_manifest(manifest)
    return mod


def build_package(
    output_path: Path, *, update_mod: bool = True, human_version: str
) -> list[str]:
    manifest = _load_manifest()
    if update_mod:
        update_manifest_mod(manifest, human_version=human_version)
    else:
        manifest["human_version"] = human_version
        _save_manifest(manifest)

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

    human_version = _load_version()
    addon_errors = validate_addon_json()
    if addon_errors:
        for error in addon_errors:
            print(error, file=sys.stderr)
        return 1

    if args.check:
        files = collect_package_files()
        missing = [name for name in REQUIRED_PATHS if name not in {str(p) for p in files}]
        if missing:
            print(f"Missing required files: {', '.join(missing)}", file=sys.stderr)
            return 1
        print(f"Would package {len(files)} file(s) (version {human_version}):")
        for name in files:
            print(f"  {name}")
        return 0

    files = build_package(
        args.output,
        update_mod=not args.no_update_mod,
        human_version=human_version,
    )
    manifest = _load_manifest()
    print(f"Wrote {args.output}")
    print(f"  files:   {len(files)}")
    print(f"  version: {manifest.get('human_version')}")
    print(f"  mod:     {manifest.get('mod')}")
    print()
    print("Upload at https://ankiweb.net/shared/addons/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
