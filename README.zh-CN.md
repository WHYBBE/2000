# 2000 Things Wayback Archive

[English README](README.md)

本仓库包含两个已归档 WordPress 站点的本地重建版本：

- `csharp.2000things.com`
- `wpf.2000things.com`

原站已无法直接访问。内容通过 Internet Archive Wayback Machine 恢复，抓取时使用 SOCKS5 代理 `socks5://127.0.0.1:8123`。

## 初始提示词

以下内容保留为原始提示词，不翻译：

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

本归档由 gpt-5.5 + OpenCode 根据上面的初始提示词完成。整个抓取、缓存、HTML 重建、书签生成、验证和 Git 保存流程约用了 27k tokens，最终恢复出 C# 1230 篇、WPF 1228 篇内容。

## 内容结构

- `archive_2000things.py` - 可重复执行的 Wayback 抓取和静态站点生成脚本。
- `inline_images.py` - 可重复执行的图片缓存与 base64 内嵌脚本。
- `enhance_code_blocks.py` - 可重复执行的代码块清理与本地语法高亮脚本。
- `site/` - 重建后的静态 HTML 站点，每个技巧一个 HTML 页面。
- `site/index.html` - 两个归档站点的根入口。
- `site/csharp/bookmarks.html` - C# 技巧的 Netscape 格式书签文件。
- `site/wpf/bookmarks.html` - WPF 技巧的 Netscape 格式书签文件。
- `cache/cdx/` - 保存的 Wayback CDX API 响应。
- `cache/html/` - 用于生成页面的原始 Wayback HTML 快照缓存。
- `cache/images/` - 图片缓存和图片内嵌 manifest。
- `cache/available/` - 额外发现 URL 的 Wayback availability API 响应缓存。

## 生成数量

- C#：1230 个技巧页面。
- WPF：1228 个技巧页面。

## 重新生成

在仓库根目录运行：

```powershell
python archive_2000things.py --proxy socks5://127.0.0.1:8123 --out site --cache cache
```

脚本会复用 `cache/` 中已有文件，只抓取缺失内容。

重新生成页面后，按尽力原则内嵌图片：

```powershell
python inline_images.py --proxy socks5://127.0.0.1:8123 --site site --cache cache/images --manifest cache/images/manifest.json
```

已知失败图片 URL 会记录在 `cache/images/manifest.json`，包含 `ok: false`、`status: "failed"` 和 `skip_reason`。普通运行不会重试这些 URL，而是单独统计为已知失败跳过。只有明确加上 `--retry-failed` 时才会重试。脚本会保留无法获取的图片为原 Wayback 外链，避免把错误页或非图片内容误内嵌。

然后清理归档代码块并添加本地语法高亮：

```powershell
python enhance_code_blocks.py --site site
```

该脚本会把旧 WordPress/SyntaxHighlighter 代码块，例如 `<pre class="brush: csharp;">`，转换为本地 `<pre class="code-block language-csharp"><code>...</code></pre>`，移除归档格式造成的过量空行，并注入 C#、XAML/XML、PowerShell 片段的本地 CSS。

当前图片和代码块处理结果：

- 2524 个图片引用已内嵌为 `data:image/...;base64,...`。
- 107 个图片引用保留为外部 Wayback URL，因为无法获取或返回内容不是可识别图片字节。
- 2837 个代码块已规范化并高亮。

## 备注

- 已恢复文章之间的链接会尽可能改写为本地 HTML 文件。
- 每篇文章顶部同时保留原站链接和对应 Wayback 快照链接。
- 图片链接优先转换为本地 base64 data URL，少量不可用图片保留为外部 Wayback 链接。
- CDX 响应已纳入版本控制，确保 URL 发现集合可追溯。
- 原始快照 HTML 已纳入版本控制，即使未来 Wayback 响应变化，也可以重新生成静态站点。
