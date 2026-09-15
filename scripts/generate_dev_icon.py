#!/usr/bin/env python3
"""Generate the red DEV .icns with a test-tube glyph (not the prod satellite)."""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPNGFileType,
)

ICON_SLOTS = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

_TEST_TUBE = "🧪"


def _render_png(path: Path, size: int) -> None:
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()

    # Red field so DEV is visually distinct from production.
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.78, 0.08, 0.08, 1.0).setFill()
    radius = size * 0.2
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, size, size),
        radius,
        radius,
    ).fill()

    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setAlignment_(1)  # center

    # Primary mark: test tube (matches menubar DEV glyph).
    tube_size = max(size * 0.55, 10.0)
    tube_attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(tube_size),
        NSForegroundColorAttributeName: NSColor.whiteColor(),
        NSParagraphStyleAttributeName: paragraph,
    }
    tube = NSAttributedString.alloc().initWithString_attributes_(_TEST_TUBE, tube_attrs)
    tube_height = tube_size * 1.15
    tube.drawInRect_(
        NSMakeRect(0, (size - tube_height) / 2.0 + size * 0.04, size, tube_height)
    )

    # Small DEV caption under the glyph for Finder/Dock clarity at large sizes.
    if size >= 64:
        caption_size = max(size * 0.12, 8.0)
        caption_attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(caption_size),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSParagraphStyleAttributeName: paragraph,
        }
        caption = NSAttributedString.alloc().initWithString_attributes_(
            "DEV", caption_attrs
        )
        caption.drawInRect_(
            NSMakeRect(0, size * 0.06, size, caption_size * 1.3)
        )

    image.unlockFocus()
    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    if bitmap is None:
        raise RuntimeError("Unable to create bitmap representation for icon.")
    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {})
    if png_data is None:
        raise RuntimeError("Unable to encode PNG data for icon.")
    if not png_data.writeToFile_atomically_(str(path), True):
        raise RuntimeError(f"Unable to write icon PNG: {path}")


def _build_iconset(iconset_dir: Path) -> None:
    iconset_dir.mkdir(parents=True, exist_ok=True)
    for filename, size in ICON_SLOTS.items():
        _render_png(iconset_dir / filename, size)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a red DEV .icns with a test-tube glyph."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Absolute path for output .icns file.",
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dev-iconset-") as tmp:
        iconset_dir = Path(tmp) / "dev.iconset"
        _build_iconset(iconset_dir)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(output)],
            check=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
