# 2000 Things Wayback Archive

This repository contains a local rebuild of two archived WordPress sites:

- `csharp.2000things.com`
- `wpf.2000things.com`

The original sites are no longer directly accessible. Content was recovered from the Internet Archive Wayback Machine using the SOCKS5 proxy `socks5://127.0.0.1:8123`.

## Contents

- `archive_2000things.py` - repeatable fetch/generation script.
- `site/` - rebuilt static HTML site, with one HTML page per tip.
- `site/index.html` - root index for both archives.
- `site/csharp/bookmarks.html` - Netscape-style bookmark file for C# tips.
- `site/wpf/bookmarks.html` - Netscape-style bookmark file for WPF tips.
- `cache/cdx/` - saved Wayback CDX API responses.
- `cache/html/` - saved raw Wayback HTML snapshots used to generate pages.
- `cache/available/` - saved Wayback availability API responses for extra discovered URLs.

## Generated Counts

- C#: 1230 generated tip pages.
- WPF: 1228 generated tip pages.

## Regeneration

Run from the repository root:

```powershell
python archive_2000things.py --proxy socks5://127.0.0.1:8123 --out site --cache cache
```

The script is cache-aware. Existing files in `cache/` are reused, and missing files are fetched from Wayback.

## Notes

- Article links between recovered posts are rewritten to local HTML files where possible.
- Image links point to Wayback archived resources.
- The CDX responses are versioned so the discovered URL set is preserved.
- Raw snapshot HTML is versioned so the static site can be regenerated even if future Wayback responses change.
