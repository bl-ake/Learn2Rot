#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fetch platform-tagged wheels into vendor/<tag>/ for release packaging.

Cross-platform pip installs (works on Linux CI and macOS/Windows hosts):

    python fetch_vendor.py              # all supported tags
    python fetch_vendor.py --host-only  # current machine tag only

Anki 25.x ships Python 3.13; wheels are pinned to that ABI.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from vendor_paths import VENDOR_TAGS, vendor_tag

ADDON_DIR = Path(__file__).resolve().parent
VENDOR_ROOT = ADDON_DIR / "vendor"
PYTHON_VERSION = "313"

# (vendor_subdir, pip --platform, requirements file, needs_rumps_sdist)
TARGETS: list[tuple[str, str, str, bool]] = [
    ("macosx_arm64", "macosx_11_0_arm64", "requirements-darwin.txt", True),
    ("macosx_x86_64", "macosx_10_13_x86_64", "requirements-darwin.txt", True),
    ("win_amd64", "win_amd64", "requirements-win32.txt", False),
]

# PyObjC ships universal2 wheels that may not match arch-specific --platform.
_PYOBJC_PIP_PLATFORM = "macosx_10_13_universal2"


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=cwd)


def _pip_install_binary(
    target: Path,
    *,
    platform: str,
    packages: list[str] | None = None,
    requirements: Path | None = None,
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-compile",
        "--target",
        str(target),
        "--platform",
        platform,
        "--python-version",
        PYTHON_VERSION,
        "--implementation",
        "cp",
        "--abi",
        f"cp{PYTHON_VERSION}",
        "--only-binary=:all:",
    ]
    if requirements is not None:
        cmd.extend(["-r", str(requirements)])
    if packages:
        cmd.extend(packages)
    _run(cmd, cwd=ADDON_DIR)


def _pip_install_sdist(target: Path, *packages: str) -> None:
    """Install pure/sdist packages (e.g. rumps) into an existing target tree."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-compile",
        "--target",
        str(target),
        "--no-deps",
        *packages,
    ]
    _run(cmd, cwd=ADDON_DIR)


def _cleanup_tree(target: Path) -> None:
    for path in sorted(target.rglob("*"), reverse=True):
        if not path.exists():
            continue
        name = path.name
        if path.is_dir() and (
            name == "__pycache__"
            or name.endswith(".dSYM")
            or name.endswith(".egg-info")
        ):
            shutil.rmtree(path, ignore_errors=True)
            continue
        if path.is_file() and name.endswith((".pyc", ".pyo")):
            path.unlink(missing_ok=True)


def _validate_tree(tag: str, target: Path) -> None:
    errors: list[str] = []
    if not (target / "pymunk").is_dir():
        errors.append("missing pymunk/")
    if not any(target.glob("_cffi_backend*")):
        errors.append("missing _cffi_backend* native module")
    if tag.startswith("macosx"):
        if not (target / "rumps").is_dir():
            errors.append("missing rumps/")
        if not (target / "objc").is_dir() and not (target / "AppKit").is_dir():
            errors.append("missing pyobjc (objc/ or AppKit/)")
    if tag == "win_amd64":
        if not (target / "pystray").is_dir():
            errors.append("missing pystray/")
        if not (target / "six").is_dir() and not (target / "six.py").is_file():
            errors.append("missing six/")
    if errors:
        raise SystemExit(f"vendor/{tag} invalid: {', '.join(errors)}")


def fetch_target(tag: str, pip_platform: str, requirements_name: str, needs_rumps: bool) -> None:
    del requirements_name  # kept in TARGETS for documentation / future use
    target = VENDOR_ROOT / tag
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    if tag.startswith("macosx"):
        # Arch-specific pymunk/cffi first.
        _pip_install_binary(
            target,
            platform=pip_platform,
            packages=["pymunk>=7.0,<8"],
        )
        # Universal2 PyObjC (compatible with both Mac arches at runtime).
        _pip_install_binary(
            target,
            platform=_PYOBJC_PIP_PLATFORM,
            packages=["pyobjc-framework-Cocoa>=10.0"],
        )
        if needs_rumps:
            _pip_install_sdist(target, "rumps>=0.4.0")
    else:
        # Install binary wheels explicitly. Do not use requirements-win32.txt with
        # --platform: pip still evaluates environment markers on the *host*, so
        # pystray's darwin-only Quartz extra breaks resolution on macOS/Linux CI.
        _pip_install_binary(
            target,
            platform=pip_platform,
            packages=[
                "pymunk>=7.0,<8",
                "Pillow>=10.0.0",
                "winrt-runtime>=3.0",
                "winrt-Windows.Foundation>=3.0",
                "winrt-Windows.Media.Control>=3.0",
            ],
        )
        _pip_install_sdist(target, "pystray>=0.19.0", "six>=1.9")

    _cleanup_tree(target)
    _validate_tree(tag, target)
    print(f"OK vendor/{tag}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-only",
        action="store_true",
        help="fetch only the vendor tag for this machine",
    )
    args = parser.parse_args(argv)

    host = vendor_tag()
    selected = []
    for tag, pip_platform, req_name, needs_rumps in TARGETS:
        if args.host_only and tag != host:
            continue
        if tag not in VENDOR_TAGS:
            raise SystemExit(f"unknown tag {tag!r} (update vendor_paths.VENDOR_TAGS)")
        selected.append((tag, pip_platform, req_name, needs_rumps))

    if not selected:
        raise SystemExit(
            f"No vendor targets for host tag {host!r}. "
            "Supported tags: " + ", ".join(VENDOR_TAGS)
        )

    VENDOR_ROOT.mkdir(parents=True, exist_ok=True)
    # Avoid inheriting host site-packages when cross-compiling.
    os.environ.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    for item in selected:
        fetch_target(*item)

    print(f"Fetched {len(selected)} vendor tree(s) under {VENDOR_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
