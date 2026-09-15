# Media Wave Technology — brand icon scaffold

Editable source marks for **MWT** (monogram) and **Media Wave Technology** (wordmark / lockup).

## Structure

```
brand/
  tokens.json                         # colors, type, export sizes
  mwt/
    mark.svg                          # primary app / avatar mark (on dark)
    mark-on-light.svg                 # inverted surface
    favicon.svg                       # 64px compact
  media-wave-technology/
    wordmark.svg                      # text-only
    lockup.svg                        # mark + wordmark (on light)
    lockup-on-dark.svg
  exports/
    png/                              # concept rasters + generated PNGs
    ico/                              # reserved for Windows favicon packs
    icns/                             # reserved for macOS .icns (iconutil on macOS)
  README.md
```

## Palette (v0 scaffold)

| Token | Hex | Use |
|---|---|---|
| `ink` | `#0B1C24` | Primary dark field |
| `foam` | `#F2F7F8` | Light glyph / light field |
| `wave` | `#2EC4B6` | Accent wave / signal |
| `muted` | `#8AA3AB` | Secondary word (“Technology”) |

Do not use purple/indigo defaults or warm cream+terracotta stacks for this brand.

## Concept rasters (AI draft — not final)

Committed under `exports/png/` for critique:

- `mwt-mark-concept.png` — square monogram direction
- `media-wave-technology-lockup-concept.png` — horizontal lockup direction

Generated size matrix PNGs (`mwt-mark-*.png`, lockups, favicons) are produced by `scripts/render_brand_exports.py`.

Treat SVGs as the editable source of truth. Concepts guide letterform refinement.

## Clearspace & sizing

- Mark: keep ≥12% padding inside the rounded square (see `tokens.json`).
- Lockup: minimum rendered height 24px; prefer ≥32px in UI chrome.
- Never stretch the lockup — scale uniformly.

## Export

```bash
# Rasterize SVG masters to the PNG size matrix (requires cairosvg)
python3 scripts/render_brand_exports.py

# Optional: build macOS .icns from the mark size matrix (macOS only)
python3 scripts/render_brand_exports.py --icns
```

## Integration notes (this repo)

| Surface | Asset |
|---|---|
| Menubar / app icon | `mwt/mark.svg` → PNG/ICNS via export script |
| README / docs header | `media-wave-technology/lockup.svg` |
| Favicon / PWA | `mwt/favicon.svg` (+ PNG pack) |
| DEV builds | Keep existing red `DEV` overlay (`scripts/generate_dev_icon.py`); do not replace with MWT for DEV |

## Open design follow-ups

1. Replace placeholder system-font monogram with custom MWT letterforms matching the concept raster.
2. Decide whether product apps use the MWT mark alone or a product-specific badge.
3. Produce final `.icns` / `.ico` on a macOS host (`iconutil`) once SVGs are approved.
