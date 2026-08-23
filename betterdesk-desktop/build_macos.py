#!/usr/bin/env python3
"""Build and package BetterDesk Desktop for macOS.

This complements build.py, which currently ships Windows/Linux targets only.
The produced app is ad-hoc signed so the injected Rust cdylib is part of the
bundle signature. Release notarization can be added later when Apple signing
credentials are available.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
FLUTTER = ROOT / "flutter"
DIST = ROOT / "dist"
TARGET = ROOT / "target"
VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def require(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"Missing build tool: {name}")
    return resolved


def architecture_label() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x64"
    return machine.replace(" ", "-")


def ensure_macos_project(flutter: str) -> None:
    run([flutter, "config", "--enable-macos-desktop"], cwd=FLUTTER)
    if not (FLUTTER / "macos").exists():
        run(
            [
                flutter,
                "create",
                "--platforms=macos",
                "--project-name",
                "betterdesk_desktop",
                ".",
            ],
            cwd=FLUTTER,
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    flutter = require("flutter")
    require("cargo")
    require("rustc")
    require("protoc")
    require("codesign")
    require("ditto")
    require("hdiutil")

    arch = architecture_label()
    DIST.mkdir(exist_ok=True)
    ensure_macos_project(flutter)

    run(["cargo", "build", "--release", "--target-dir", str(TARGET)])
    dylib = TARGET / "release" / "libbetterdesk_desktop.dylib"
    if not dylib.is_file():
        raise SystemExit(f"Rust core library was not produced: {dylib}")

    run([flutter, "pub", "get"], cwd=FLUTTER)
    run([flutter, "build", "macos", "--release"], cwd=FLUTTER)

    release_dir = FLUTTER / "build" / "macos" / "Build" / "Products" / "Release"
    apps = sorted(release_dir.glob("*.app"))
    if not apps:
        raise SystemExit(f"Flutter macOS bundle was not produced in {release_dir}")

    stage = DIST / "staging" / f"macos-{arch}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    app = stage / "BetterDesk Desktop.app"
    shutil.copytree(apps[0], app, symlinks=True)

    frameworks = app / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    bundled_dylib = frameworks / dylib.name
    shutil.copy2(dylib, bundled_dylib)

    # Re-sign after injecting the Rust library. This is intentionally ad-hoc;
    # it makes the bundle internally consistent without requiring secrets.
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(bundled_dylib)])
    run(
        [
            "codesign",
            "--force",
            "--deep",
            "--sign",
            "-",
            "--timestamp=none",
            str(app),
        ]
    )
    run(["codesign", "--verify", "--deep", "--strict", str(app)])

    zip_path = DIST / f"BetterDesk-Desktop-{VERSION}-macos-{arch}.zip"
    if zip_path.exists():
        zip_path.unlink()
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path)])

    dmg_path = DIST / f"BetterDesk-Desktop-{VERSION}-macos-{arch}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            "BetterDesk Desktop",
            "-srcfolder",
            str(app),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
    )

    checksum_path = DIST / f"DESKTOP_CHECKSUMS-macos-{arch}.sha256"
    checksum_path.write_text(
        "\n".join(
            [
                f"{sha256(zip_path)}  {zip_path.name}",
                f"{sha256(dmg_path)}  {dmg_path.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print("Produced:")
    for output in (zip_path, dmg_path, checksum_path):
        print(f"  {output}")


if __name__ == "__main__":
    main()
