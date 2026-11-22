# -*- coding: utf-8 -*-
"""Download Argos Translate language packs for offline use (run in CI)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "profiles" / "argos_packages"
OUT.mkdir(parents=True, exist_ok=True)

WANTED = [
    ("en", "fa"), ("fa", "en"),
    ("en", "ar"), ("ar", "en"),
    ("en", "de"), ("de", "en"),
    ("en", "fr"), ("fr", "en"),
    ("en", "es"), ("es", "en"),
    ("en", "ru"), ("ru", "en"),
    ("en", "zh"), ("zh", "en"),
    ("en", "tr"), ("tr", "en"),
    ("en", "pt"), ("pt", "en"),
    ("en", "it"), ("it", "en"),
]


def main() -> int:
    try:
        import argostranslate.package as pkg
    except Exception as e:
        print("argostranslate not installed:", e)
        return 1
    print("Updating package index...")
    try:
        pkg.update_package_index()
    except Exception as e:
        print("index update failed:", e)
        return 1
    available = pkg.get_available_packages()
    print("available packages:", len(available))
    got = 0
    for fr, to in WANTED:
        matches = [p for p in available if p.from_code == fr and p.to_code == to]
        if not matches:
            print(f"SKIP missing {fr}->{to}")
            continue
        p = matches[0]
        print(f"Downloading {fr}->{to} ...")
        try:
            path = p.download()
            dest = OUT / f"{fr}_{to}.argosmodel"
            data = Path(path).read_bytes()
            dest.write_bytes(data)
            pkg.install_from_path(str(dest))
            print(f"  OK {dest.name} ({len(data)} bytes)")
            got += 1
        except Exception as e:
            print(f"  FAIL {fr}->{to}: {e}")
    print(f"Done. saved={got} dir={OUT}")
    return 0 if got > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
