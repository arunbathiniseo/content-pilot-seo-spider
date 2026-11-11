# Content Pilot Spider — brand

An original identity for the crawler, independent of contentpilot.cloud. Two colours, one typeface family, one
mark. It is meant to look like a serious technical tool, not a SaaS landing page.

## Idea

The mark is a spider drawn as a crawl graph: the green hub is the start URL, the eight legs are crawl paths, the
feet are crawled pages. At 512 px it is clearly a spider; at 16 px it is a node-and-spokes glyph, which is what
the product does. Legs are straight with one knee, feet are dots, nothing is cute.

## Files

| File | Use |
| --- | --- |
| `mark.svg` | Ink tile, light glyph, green hub. App icon, favicon, avatars, anywhere square. |
| `mark-mono.svg` | Single-ink glyph, no tile, uses `currentColor`. Topbar, splash, toolbars, print. |
| `logo-horizontal.svg` / `-dark.svg` | Mark + wordmark for light / dark backgrounds. README, docs, decks. |
| `out/icon.ico` | Windows app icon, 16–256 px. Used by the window and the Desktop shortcut. |
| `out/icon-{16…1024}.png` | PNG sizes for stores, docs, OS bundles. |
| `out/favicon-32.png` | Browser favicon (the app serves `mark.svg` as its favicon). |
| `out/social-card.png` | 1200×630 Open Graph / Twitter card. |
| `out/readme-banner.png` | 1600×400 README / GitHub header. |

Rebuild after editing an SVG: `.venv\Scripts\python.exe branding\build.py` (needs the Playwright Chromium the app
already uses).

## Colour

| Name | Value | Use |
| --- | --- | --- |
| Ink | `#15171a` | Tile, wordmark on light, dark grounds |
| Paper | `#f4f5f7` | Glyph on ink, light grounds (same as the app background) |
| Signal | `#2ea043` on ink, `#1f7a3f` on paper | The hub, the subline, one rule or edge per layout. Never a fill behind text. |
| Muted | `#9aa3ad` dark / `#3d434b` light | Secondary copy |

Nothing else. No gradients, no glows, no purple. Issue colours inside the app (red/amber/blue) are UI semantics
and never appear in brand material.

## Type

IBM Plex Sans 600 for the wordmark, tracked −4 %. IBM Plex Mono 500, uppercase, +20 % tracking for the subline
and small labels ("SEO CRAWLER · LOCAL · OPEN SOURCE"). Fallbacks: Segoe UI / system-ui and Consolas. The SVG
lockups name the fonts; use the PNG exports where the font cannot be guaranteed.

## Layout rules

Clear space around the mark equals the hub diameter (¼ of the tile). Minimum sizes: mark 16 px, horizontal lockup
180 px wide. The green edge/rule is a single accent per composition: one left edge, one short rule, or the hub —
not all three.

## Don'ts

No rotating, outlining or recolouring the mark; no shadows; no stretching the wordmark; no emoji spider; no
gradient tiles.
