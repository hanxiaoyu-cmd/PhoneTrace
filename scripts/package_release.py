"""Package the complete portable directory and generate a SHA-256 checksum."""
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-root", default="dist", help="Build output directory, relative to the project or absolute (default: dist)")
    args = parser.parse_args()
    dist = Path(args.dist_root)
    dist = (dist if dist.is_absolute() else root / dist).resolve()
    if dist == root or not dist.is_relative_to(root):
        parser.error("--dist-root must be a subdirectory of this project")
    app = dist / "PhoneTrace"
    if not (app / "PhoneTrace.exe").is_file():
        raise SystemExit("Run build.ps1 before packaging a release.")
    archive = dist / "PhoneTrace-Windows-x64.zip"
    excluded = {"__pycache__", "records", "test-output", ".venv", ".git"}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(app.rglob("*")):
            relative = file.relative_to(app)
            if file.is_file() and not excluded.intersection(relative.parts) and file.suffix != ".log":
                bundle.write(file, file.relative_to(dist))
    with zipfile.ZipFile(archive) as bundle:
        damaged = bundle.testzip()
        if damaged:
            raise SystemExit(f"Archive integrity check failed: {damaged}")
        required = {
            "PhoneTrace/PhoneTrace.exe", "PhoneTrace/tools/platform-tools/adb.exe",
            "PhoneTrace/source/main.py", "PhoneTrace/README.md", "PhoneTrace/docs/accuracy.md",
        }
        if not required.issubset(bundle.namelist()):
            raise SystemExit("Portable package is missing required files.")
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Created {archive.name} ({archive.stat().st_size / 1048576:.1f} MiB)")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
