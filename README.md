# 2000 Things Wayback Archive

This repository contains a local rebuild of two archived WordPress sites:

- `csharp.2000things.com`
- `wpf.2000things.com`

The original sites are no longer directly accessible. Content was recovered from the Internet Archive Wayback Machine using the SOCKS5 proxy `socks5://127.0.0.1:8123`.

## 初始提示词

```text
使用工具抓取网页中所有相关内容
http://csharp.2000things.com/
https://wpf.2000things.com/
这两个网页已经不可以访问了
应该使用代理和网页历史存档获取原始内容
https://web.archive.org/
代理：socks5://127.0.0.1:8123
我需要尽可能完整的内容
整理成html的形式，每个技巧一个页面，需要重新制作书签等内容
```

## 实现备注

本归档由 gpt-5.5 + OpenCode 根据上面的初始提示词完成。整个抓取、缓存、HTML 重建、书签生成、验证和 Git 保存流程只用了约 27k tokens，最终恢复出 C# 1230 篇、WPF 1228 篇内容。

## Contents

- `archive_2000things.py` - repeatable fetch/generation script.
- `inline_images.py` - repeatable best-effort image caching and base64 inlining script.
- `enhance_code_blocks.py` - repeatable code block cleanup and local syntax highlighting script.
- `site/` - rebuilt static HTML site, with one HTML page per tip.
- `site/index.html` - root index for both archives.
- `site/csharp/bookmarks.html` - Netscape-style bookmark file for C# tips.
- `site/wpf/bookmarks.html` - Netscape-style bookmark file for WPF tips.
- `cache/cdx/` - saved Wayback CDX API responses.
- `cache/html/` - saved raw Wayback HTML snapshots used to generate pages.
- `cache/images/` - saved images fetched while converting external image links to base64 data URLs.
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

After regenerating pages, inline images on a best-effort basis:

```powershell
python inline_images.py --proxy socks5://127.0.0.1:8123 --site site --cache cache/images --manifest cache/images/manifest.json
```

Use `--retry-failed` only when you explicitly want to spend extra time retrying URLs already known to fail. The script intentionally keeps failed images as their original Wayback links instead of embedding broken HTML/error responses.

Then normalize archived code blocks and add local syntax highlighting:

```powershell
python enhance_code_blocks.py --site site
```

This converts old WordPress/SyntaxHighlighter blocks such as `<pre class="brush: csharp;">` into local `<pre class="code-block language-csharp"><code>...</code></pre>` blocks, removes excessive blank lines caused by archived formatting, and injects local CSS for C#, XAML/XML, and PowerShell snippets.

Current image inlining result:

- 2524 image references embedded as `data:image/...;base64,...`.
- 107 image references left as external Wayback URLs because they could not be fetched or did not return recognizable image bytes.
- 2837 code blocks normalized and highlighted.

## Notes

- Article links between recovered posts are rewritten to local HTML files where possible.
- Image links point to Wayback archived resources.
- Most image links have been converted to local base64 data URLs. A small number remain external because the archived image bytes were unavailable.
- The CDX responses are versioned so the discovered URL set is preserved.
- Raw snapshot HTML is versioned so the static site can be regenerated even if future Wayback responses change.
