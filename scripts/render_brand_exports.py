#!/usr/bin/env python3
"""Rasterize brand SVG masters into the export size matrix.

Requires: pip install cairosvg
Optional (macOS): iconutil for .icns packing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
TOKENS = BRAND / "tokens.json"
PNG_OUT = BRAND / "exports" / "png"
ICO_OUT = BRAND / "exports" / "ico"
ICNS_OUT = BRAND / "exports" / "icns"

MASTERS = {
    "mwt-mark": BRAND / "mwt" / "mark.svg",
    "mwt-mark-on-light": BRAND / "mwt" / "mark-on-light.svg",
    "mwt-favicon": BRAND / "mwt" / "favicon.svg",
    "mwt-wordmark": BRAND / "media-wave-technology" / "wordmark.svg",
    "mwt-lockup": BRAND / "media-wave-technology" / "lockup.svg",
    "mwt-lockup-on-dark": BRAND / "media-wave-technology" / "lockup-on-dark.svg",
}


def _load_tokens() -> dict:
    return json.loads(TOKENS.read_text())


def _require_cairosvg():
    try:
        import cairosvg  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "cairosvg is required. Install with: pip install cairosvg"
        ) from exc
    return __import__("cairosvg")


def render_png(svg: Path, dest: Path, size: int) -> None:
    cairosvg = _require_cairosvg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        url=str(svg),
        write_to=str(dest),
        output_width=size,
        output_height=size if "lockup" not in dest.stem and "wordmark" not in dest.stem else None,
    )


def render_mark_matrix(name: str, svg: Path, sizes: list[int]) -> None:
    for size in sizes:
        out = PNG_OUT / f"{name}-{size}.png"
        cairosvg = _require_cairosvg()
        cairosvg.svg2png(
            url=str(svg),
            write_to=str(out),
            output_width=size,
            output_height=size,
        )
        print(f"wrote {out.relative_to(ROOT)}")


def render_wide(name: str, svg: Path, height: int) -> None:
    cairosvg = _require_cairosvg()
    out = PNG_OUT / f"{name}-h{height}.png"
    cairosvg.svg2png(
        url=str(svg),
        write_to=str(out),
        output_height=height,
    )
    print(f"wrote {out.relative_to(ROOT)}")


def build_icns(mark_svg: Path) -> None:
    if not shutil.which("iconutil"):
        raise SystemExit("iconutil not found (macOS only). Skip --icns on Linux.")
    tokens = _load_tokens()
    slots = tokens["exports"]["icns_slots"]
    ICNS_OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mwt-iconset-") as tmp:
        iconset = Path(tmp) / "mwt.iconset"
        iconset.mkdir()
        cairosvg = _require_cairosvg()
        for filename, size in slots.items():
            cairosvg.svg2png(
                url=str(mark_svg),
                write_to=str(iconset / filename),
                output_width=size,
                output_height=size,
            )
        out = ICNS_OUT / "mwt.icns"
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
            check=True,
        )
        print(f"wrote {out.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--icns",
        action="store_true",
        help="Also pack mwt/mark.svg into exports/icns/mwt.icns (macOS).",
    )
    args = parser.parse_args()

    tokens = _load_tokens()
    sizes = tokens["exports"]["mark_png_sizes"]
    favicon_sizes = tokens["exports"]["favicon_sizes"]

    PNG_OUT.mkdir(parents=True, exist_ok=True)
    ICO_OUT.mkdir(parents=True, exist_ok=True)

    render_mark_matrix("mwt-mark", MASTERS["mwt-mark"], sizes)
    render_mark_matrix("mwt-mark-on-light", MASTERS["mwt-mark-on-light"], sizes)
    render_mark_matrix("mwt-favicon", MASTERS["mwt-favicon"], favicon_sizes)
    for height in (48, 96, 192):
        render_wide("mwt-wordmark", MASTERS["mwt-wordmark"], height)
        render_wide("mwt-lockup", MASTERS["mwt-lockup"], height)
        render_wide("mwt-lockup-on-dark", MASTERS["mwt-lockup-on-dark"], height)

    if args.icns:
        build_icns(MASTERS["mwt-mark"])

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
