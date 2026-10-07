# AutoExperten brand assets

| File | What it is |
| --- | --- |
| `official/AutoExperten_Logo.png` | **Official** wordmark from https://www.autoexperten-rn.de/AutoExperten_Logo.png (1536×1024, transparent). Byte-identical copy – never edit. |
| `official/AutoExperten_Icon.png` | **Official** "AE" monogram (website app icon, 500×500). Byte-identical copy. |
| `official/AutoExperten_Wordmark_small.png` | Official small wordmark (website favicon-large, 840×185). |
| `autoexperten-logo.png` | UI logo for light backgrounds: official wordmark, transparent margin trimmed, scaled to 760 px. |
| `autoexperten-logo-light.png` | UI logo for dark backgrounds: same pixels, only the neutral-gray "Auto" letters recoloured to white (blue unchanged). **Derived** – replace with an official light version if AutoExperten has one. |

The website only provides PNG artwork (no SVG). Logo colours measured from
the official file: blue `#0788EA`, gray `#403F3F`.

App icons in `/public/icons/` are generated from the official monogram on a
white background. The image processor uses `official/AutoExperten_Logo.png`
in full resolution for the showroom brand wall.

Configuration: `LOGO_ASSETS` in `src/config/brand.ts` (`useAssetFiles: true`).
Do not redraw, recolour or approximate the logo.
