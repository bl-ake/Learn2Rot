#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build an AnkiWeb-ready .ankiaddon package for Learn2Rot.

See https://addon-docs.ankiweb.net/sharing.html

Usage:
    python fetch_vendor.py         # populate vendor/<tag>/ trees first
    python package.py              # write dist/Learn2Rot.ankiaddon
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

from vendor_paths import VENDOR_TAGS

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
    "vendor_paths.py",
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
        "requirements-common.txt",
        "requirements-darwin.txt",
        "requirements-win32.txt",
        "pytest.ini",
        "package.py",
        "fetch_vendor.py",
    }
)
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".ankiaddon"})


def _load_version() -> str:
    spec = importlib.util.spec_from_file_location("learn2rot_version", VERSION_PATH)
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


def validate_vendor_trees() -> list[str]:
    """Require platform-tagged vendor trees from fetch_vendor.py."""
    errors: list[str] = []
    vendor_root = ADDON_DIR / "vendor"
    if not vendor_root.is_dir():
        return [
            "Missing vendor/ (run: python fetch_vendor.py)",
        ]
    for tag in VENDOR_TAGS:
        target = vendor_root / tag
        if not target.is_dir():
            errors.append(f"missing vendor/{tag}/ (run: python fetch_vendor.py)")
            continue
        if not (target / "pymunk").is_dir():
            errors.append(f"vendor/{tag}/ missing pymunk/")
        if not any(target.glob("_cffi_backend*")):
            errors.append(f"vendor/{tag}/ missing _cffi_backend*")
        if tag.startswith("macosx") and not (target / "rumps").is_dir():
            errors.append(f"vendor/{tag}/ missing rumps/")
        if tag == "win_amd64" and not (target / "pystray").is_dir():
            errors.append(f"vendor/{tag}/ missing pystray/")
    return errors


def _should_exclude(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    # Only ship tagged vendor trees — skip legacy flat vendor/ natives.
    parts = path.parts
    if parts and parts[0] == "vendor":
        if len(parts) < 2 or parts[1] not in VENDOR_TAGS:
            return True
    return False


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
    vendor_errors = validate_vendor_trees()
    if vendor_errors:
        raise SystemExit("\n".join(vendor_errors))

    manifest = _load_manifest()
    if update_mod:
        update_manifest_mod(manifest, human_version=human_version)
    else:
        manifest["human_version"] = human_version
        _save_manifest(manifest)

    files = collect_package_files()
    packaged = {p.as_posix() for p in files}
    missing = [name for name in REQUIRED_PATHS if name not in packaged]
    if missing:
        raise SystemExit(f"Package is missing required files: {', '.join(missing)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(ADDON_DIR / relative, arcname=relative.as_posix())

    return [path.as_posix() for path in files]


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
        default=OUTPUT_DIR / "Learn2Rot.ankiaddon",
        help=f"output .ankiaddon path (default: {OUTPUT_DIR / 'Learn2Rot.ankiaddon'})",
    )
    args = parser.parse_args(argv)

    human_version = _load_version()
    addon_errors = validate_addon_json()
    if addon_errors:
        for error in addon_errors:
            print(error, file=sys.stderr)
        return 1

    vendor_errors = validate_vendor_trees()
    if vendor_errors:
        for error in vendor_errors:
            print(error, file=sys.stderr)
        return 1

    if args.check:
        files = collect_package_files()
        packaged = {p.as_posix() for p in files}
        missing = [name for name in REQUIRED_PATHS if name not in packaged]
        if missing:
            print(f"Missing required files: {', '.join(missing)}", file=sys.stderr)
            return 1
        print(f"Would package {len(files)} file(s) (version {human_version}):")
        for path in files:
            print(f"  {path.as_posix()}")
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
