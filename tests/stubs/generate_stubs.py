#!/usr/bin/env python3
"""Generate no-op stub shared libraries for headless Qt smoke tests.

The CI/sandbox image lacks libGL, libEGL, libdbus and libxkbcommon. Qt only
needs the symbols resolvable at load time when running the ``offscreen``
platform plugin, so we emit zero-stubs with the correct sonames and symbol
versions. Real desktop/Windows builds use the genuine system libraries —
this script is never invoked there.
"""
from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QT_LIB = ROOT / ".venv" / "lib" / f"python{sysconfig.get_python_version()}" / \
    "site-packages" / "PySide6" / "Qt" / "lib"

LIBS = {
    "libGL.so.1":       {"prefixes": ("gl",),       "version": None},
    "libEGL.so.1":      {"prefixes": ("egl",),      "version": None},
    "libdbus-1.so.3":   {"prefixes": ("dbus_",),    "version": "LIBDBUS_1_3"},
    "libxkbcommon.so.0": {"prefixes": ("xkb_",),    "version": "V_0.5.0"},
}


def collect_symbols() -> dict[str, list[str]]:
    # Scan every Qt shared object, including platform plugins: plugins such as
    # libqoffscreen.so resolve glX*/egl* symbols at dlopen() time.
    roots = [QT_LIB, QT_LIB.parent / "plugins"]
    undef = set()
    for root in roots:
        for path in root.rglob("*.so*"):
            if not path.is_file():
                continue
            out = subprocess.run(
                ["nm", "-D", "--undefined-only", str(path)],
                capture_output=True, text=True,
            )
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    undef.add(parts[1])

    result: dict[str, list[str]] = {}
    for soname, spec in LIBS.items():
        names = set()
        for symbol in undef:
            bare = symbol.split("@", 1)[0]
            if bare.startswith(spec["prefixes"]):
                names.add(bare)
        result[soname] = sorted(names)
    return result


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = collect_symbols()
    for soname, names in symbols.items():
        spec = LIBS[soname]
        c_file = out_dir / f"{soname}.c"
        c_file.write_text(
            "/* generated no-op stub, do not edit */\n"
            + "\n".join(f"int {name}() {{ return 0; }}" for name in names)
            + "\n",
            encoding="utf-8",
        )
        map_file = out_dir / f"{soname}.map"
        if spec["version"]:
            map_file.write_text(
                f"{spec['version']} {{\n  global:\n"
                + "".join(f"    {name};\n" for name in names)
                + "  local: *;\n};\n",
                encoding="utf-8",
            )
            link_opts = ["-Wl,--version-script," + str(map_file)]
        else:
            link_opts = []
        subprocess.run(
            ["gcc", "-shared", "-fPIC", str(c_file), "-o", str(out_dir / soname),
             "-Wl,-soname," + soname, *link_opts],
            check=True,
        )
        print(f"built {soname} with {len(names)} symbols")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "build"
    main(target)
