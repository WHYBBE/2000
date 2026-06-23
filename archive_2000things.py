#!/usr/bin/env python3
"""Fetch 2000 Things posts from the Wayback Machine and rebuild local HTML.

The script intentionally uses only Python's standard library plus curl.exe so it
can run in this workspace without installing dependencies.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse


SITES = {
    "csharp": {
        "host": "csharp.2000things.com",
        "title": "2,000 Things You Should Know About C#",
    },
    "wpf": {
        "host": "wpf.2000things.com",
        "title": "2,000 Things You Should Know About WPF",
    },
}

POST_RE = re.compile(r"^/(20\d\d)/(\d\d)/(\d\d)/([^/?#]+)/?$")
WAYBACK_RE = re.compile(r"^/web/(\d{14})(?:[a-z_]+)?/(https?://.+)$")
COMMENT_OR_PAGED_RE = re.compile(r"/(?:comment-page-|page/\d+|feed/?$)|[?&](?:replytocom|share)=", re.I)
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?.*)?$", re.I)


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    original: str
    status: str
    mimetype: str
    digest: str


@dataclass
class Post:
    site: str
    title: str
    date: str
    original_url: str
    timestamp: str
    slug: str
    html: str
    text: str
    categories: list[str]
    tags: list[str]


class ElementExtractor(HTMLParser):
    def __init__(self, wanted: set[str]):
        super().__init__(convert_charrefs=False)
        self.wanted = wanted
        self.depth = 0
        self.current: list[str] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth or tag in self.wanted:
            self.current.append(render_start(tag, attrs))
            self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth or tag in self.wanted:
            self.current.append(render_start(tag, attrs, close=True))

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.current.append(f"</{tag}>")
            self.depth -= 1
            if self.depth == 0:
                self.items.append("".join(self.current))
                self.current = []

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.current.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self.depth:
            self.current.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.depth:
            self.current.append(f"&#{name};")


class TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "pre", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif self.skip == 0 and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif self.skip == 0 and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip == 0:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()


def render_start(tag: str, attrs: list[tuple[str, str | None]], close: bool = False) -> str:
    rendered = [tag]
    for key, value in attrs:
        if value is None:
            rendered.append(html.escape(key))
        else:
            rendered.append(f'{html.escape(key)}="{html.escape(value, quote=True)}"')
    suffix = " /" if close else ""
    return "<" + " ".join(rendered) + suffix + ">"


def run_curl(url: str, proxy: str, timeout: int = 45) -> bytes:
    cmd = [
        "curl.exe",
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "4",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--connect-timeout",
        "15",
        "--max-time",
        str(timeout),
    ]
    if proxy:
        parsed = urlparse(proxy)
        if parsed.scheme.startswith("socks"):
            cmd += ["--socks5", f"{parsed.hostname}:{parsed.port}"]
        else:
            cmd += ["--proxy", proxy]
    cmd.append(url)
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT)


def load_or_fetch(path: Path, url: str, proxy: str, binary: bool = False, delay: float = 0.0) -> bytes:
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    if delay:
        time.sleep(delay)
    data = run_curl(url, proxy)
    path.write_bytes(data if binary else data)
    return data


def cdx_url(host: str) -> str:
    fields = "timestamp,original,statuscode,mimetype,digest"
    params = (
        f"url={quote(host + '/*')}&output=json&fl={fields}"
        "&filter=statuscode:200&filter=mimetype:text/html&collapse=urlkey"
    )
    return f"https://web.archive.org/cdx?{params}"


def parse_cdx(data: bytes) -> list[Snapshot]:
    rows = json.loads(data.decode("utf-8", errors="replace"))
    snapshots: list[Snapshot] = []
    for row in rows[1:]:
        if len(row) >= 5:
            snapshots.append(Snapshot(*row[:5]))
    return snapshots


def canonicalize_original(url: str, default_host: str) -> str | None:
    url = html.unescape(url.strip())
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.endswith("web.archive.org"):
        match = WAYBACK_RE.match(parsed.path)
        if match:
            url = unquote(match.group(2))
            parsed = urlparse(url)
    if not parsed.netloc:
        parsed = urlparse(urljoin(f"http://{default_host}/", url))
    host = parsed.netloc.lower().split(":", 1)[0]
    if host != default_host:
        return None
    if COMMENT_OR_PAGED_RE.search(url):
        return None
    path = re.sub(r"//+", "/", parsed.path)
    match = POST_RE.match(path)
    if not match:
        return None
    return urlunparse(("http", default_host, path.rstrip("/") + "/", "", "", ""))


def post_slug(url: str) -> str:
    parsed = urlparse(url)
    match = POST_RE.match(parsed.path)
    if not match:
        return re.sub(r"[^a-z0-9]+", "-", parsed.path.strip("/").lower()).strip("-")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}-{match.group(4)}"


def post_date(url: str) -> str:
    match = POST_RE.match(urlparse(url).path)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def extract_title(raw: str, fallback: str) -> str:
    for pattern in [r"<h1[^>]*class=[\"'][^\"']*(?:entry-title|post-title)[^\"']*[\"'][^>]*>(.*?)</h1>", r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"]:
        match = re.search(pattern, raw, re.I | re.S)
        if match:
            title = clean_text(strip_tags(match.group(1)))
            title = re.sub(r"\s*[|\-]\s*2,000 Things.*$", "", title).strip()
            if title:
                return title
    return fallback


def strip_tags(fragment: str) -> str:
    parser = TextExtractor()
    parser.feed(fragment)
    return parser.text()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def extract_articles(raw: str) -> list[str]:
    extractor = ElementExtractor({"article"})
    extractor.feed(raw)
    if extractor.items:
        return extractor.items
    divs = []
    for pattern in [
        r"<div[^>]+class=[\"'][^\"']*(?:post|entry)[^\"']*[\"'][^>]*>.*?</div>\s*</div>",
        r"<div[^>]+id=[\"']post-\d+[\"'][^>]*>.*?</div>\s*</div>",
    ]:
        divs.extend(re.findall(pattern, raw, re.I | re.S))
    return divs[:1]


def extract_content(raw: str) -> str:
    for pattern in [
        r"<div[^>]+class=[\"'][^\"']*entry-content[^\"']*[\"'][^>]*>(.*?)</div>\s*(?:<footer|<div[^>]+class=[\"'][^\"']*(?:entry-utility|entry-meta|sharedaddy|wpa-about)[^\"']*)",
        r"<div[^>]+class=[\"'][^\"']*entry[^\"']*[\"'][^>]*>(.*?)</div>\s*(?:<p class=[\"']postmetadata|<div class=[\"']postmetadata)",
    ]:
        match = re.search(pattern, raw, re.I | re.S)
        if match:
            return match.group(1)
    articles = extract_articles(raw)
    if articles:
        article = articles[0]
        article = re.sub(r"<h1[^>]*>.*?</h1>", "", article, flags=re.I | re.S)
        return article
    body = re.search(r"<body[^>]*>(.*?)</body>", raw, re.I | re.S)
    return body.group(1) if body else raw


def find_categories(raw: str) -> list[str]:
    values: list[str] = []
    for rel in ["category tag", "category"]:
        for match in re.finditer(rf"<a[^>]+rel=[\"']{re.escape(rel)}[\"'][^>]*>(.*?)</a>", raw, re.I | re.S):
            text = clean_text(strip_tags(match.group(1)))
            if text and text not in values:
                values.append(text)
    return values


def find_tags(raw: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"<a[^>]+rel=[\"']tag[\"'][^>]*>(.*?)</a>", raw, re.I | re.S):
        text = clean_text(strip_tags(match.group(1)))
        if text and text not in values:
            values.append(text)
    return values


def rewrite_links(fragment: str, site: str, host: str, posts_by_url: dict[str, str], asset_dir: str, timestamp: str) -> str:
    def replace_attr(match: re.Match[str]) -> str:
        leading, attr, quote_char, value = match.group(1), match.group(2), match.group(3), html.unescape(match.group(4))
        new_value = value
        original = canonicalize_original(value, host)
        if original and original in posts_by_url:
            new_value = posts_by_url[original]
        elif value.startswith("/web/") or "web.archive.org/web/" in value:
            parsed = urlparse(value)
            wm = WAYBACK_RE.match(parsed.path)
            if wm:
                target = wm.group(2)
                original = canonicalize_original(target, host)
                if original and original in posts_by_url:
                    new_value = posts_by_url[original]
                else:
                    new_value = f"https://web.archive.org/web/{timestamp}id_/{target}"
        elif attr.lower() == "src" and IMAGE_EXT_RE.search(value):
            if value.startswith("http"):
                new_value = f"https://web.archive.org/web/{timestamp}id_/{value}"
            else:
                absolute = urljoin(f"http://{host}/", value)
                new_value = f"https://web.archive.org/web/{timestamp}id_/{absolute}"
        return f'{leading}{attr}={quote_char}{html.escape(new_value, quote=True)}{quote_char}'

    fragment = re.sub(r"\s(?:onclick|onload|onerror|onmouseover)=[\"'][^\"']*[\"']", "", fragment, flags=re.I)
    fragment = re.sub(r"(\s)(href|src)=(['\"])(.*?)\3", replace_attr, fragment, flags=re.I | re.S)
    return fragment


def discover_post_urls(snapshots: Iterable[Snapshot], host: str) -> dict[str, Snapshot]:
    posts: dict[str, Snapshot] = {}
    for snap in snapshots:
        original = canonicalize_original(snap.original, host)
        if original and original not in posts:
            posts[original] = snap
    return posts


def discover_links_from_html(raw: str, host: str) -> set[str]:
    links = set()
    for match in re.finditer(r"\s(?:href|src)=[\"']([^\"']+)[\"']", raw, re.I):
        original = canonicalize_original(match.group(1), host)
        if original:
            links.add(original)
    return links


def fetch_snapshot_for_url(url: str, cache_dir: Path, proxy: str) -> Snapshot | None:
    api = f"https://archive.org/wayback/available?url={quote(url, safe=':/')}"
    data = load_or_fetch(cache_dir / "available" / f"{post_slug(url)}.json", api, proxy, delay=0.2)
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
        closest = payload.get("archived_snapshots", {}).get("closest")
    except json.JSONDecodeError:
        return None
    if not closest or closest.get("status") != "200":
        return None
    return Snapshot(closest["timestamp"], url, "200", "text/html", "")


def fetch_post(site: str, host: str, original_url: str, snap: Snapshot, cache_dir: Path, proxy: str) -> Post | None:
    slug = post_slug(original_url)
    raw_path = cache_dir / "html" / site / f"{slug}.html"
    archive_url = f"https://web.archive.org/web/{snap.timestamp}id_/{original_url}"
    try:
        raw = load_or_fetch(raw_path, archive_url, proxy, delay=0.25).decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Fetch failed: {original_url} {exc.output.decode(errors='replace')[:200]}\n")
        return None
    title = extract_title(raw, slug)
    content = extract_content(raw)
    text = strip_tags(content)
    if len(text) < 80 or "This page is available on the web" in text:
        return None
    return Post(site, title, post_date(original_url), original_url, snap.timestamp, slug, content, text, find_categories(raw), find_tags(raw))


def page_filename(post: Post) -> str:
    return f"{post.slug}.html"


def write_site(site: str, config: dict[str, str], posts: list[Post], out_dir: Path) -> None:
    site_dir = out_dir / site
    site_dir.mkdir(parents=True, exist_ok=True)
    rel_by_url = {post.original_url: page_filename(post) for post in posts}
    for index, post in enumerate(posts):
        previous_link = page_filename(posts[index - 1]) if index > 0 else ""
        next_link = page_filename(posts[index + 1]) if index + 1 < len(posts) else ""
        content = rewrite_links(post.html, post.site, config["host"], rel_by_url, "assets", post.timestamp)
        page = render_page(config["title"], post, content, previous_link, next_link)
        (site_dir / page_filename(post)).write_text(page, encoding="utf-8")
    (site_dir / "index.html").write_text(render_index(config["title"], site, posts), encoding="utf-8")
    (site_dir / "bookmarks.html").write_text(render_bookmarks(config["title"], posts), encoding="utf-8")


def render_page(site_title: str, post: Post, content: str, previous_link: str, next_link: str) -> str:
    meta = []
    if post.categories:
        meta.append("Categories: " + ", ".join(html.escape(v) for v in post.categories))
    if post.tags:
        meta.append("Tags: " + ", ".join(html.escape(v) for v in post.tags))
    nav = []
    if previous_link:
        nav.append(f'<a href="{previous_link}">Previous</a>')
    nav.append('<a href="index.html">Index</a>')
    nav.append('<a href="bookmarks.html">Bookmarks</a>')
    if next_link:
        nav.append(f'<a href="{next_link}">Next</a>')
    return base_html(
        post.title,
        f"""
<header class="site-header">
  <a href="index.html">{html.escape(site_title)}</a>
</header>
<main class="post">
  <nav class="pager">{' | '.join(nav)}</nav>
  <h1>{html.escape(post.title)}</h1>
  <p class="meta">{html.escape(post.date)} | <a href="{html.escape(post.original_url)}">Original URL</a> | Wayback {html.escape(post.timestamp)}</p>
  <article>{content}</article>
  {'<p class="meta">' + ' | '.join(meta) + '</p>' if meta else ''}
  <nav class="pager">{' | '.join(nav)}</nav>
</main>
""",
    )


def render_index(site_title: str, site: str, posts: list[Post]) -> str:
    years: dict[str, list[Post]] = {}
    for post in posts:
        years.setdefault(post.date[:4] or "Unknown", []).append(post)
    sections = []
    for year in sorted(years):
        items = "\n".join(f'<li><time>{html.escape(p.date)}</time> <a href="{page_filename(p)}">{html.escape(p.title)}</a></li>' for p in years[year])
        sections.append(f"<h2>{html.escape(year)}</h2>\n<ol>{items}</ol>")
    return base_html(
        site_title,
        f"""
<main>
  <h1>{html.escape(site_title)}</h1>
  <p>Recovered from the Internet Archive Wayback Machine. Total posts: {len(posts)}.</p>
  <p><a href="../index.html">All sites</a> | <a href="bookmarks.html">Bookmarks</a></p>
  {''.join(sections)}
</main>
""",
    )


def render_bookmarks(site_title: str, posts: list[Post]) -> str:
    items = "\n".join(f'<DT><A HREF="{page_filename(p)}" ADD_DATE="0">{html.escape(p.date + " " + p.title)}</A>' for p in posts)
    return f"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>{html.escape(site_title)} Bookmarks</TITLE>
<H1>{html.escape(site_title)} Bookmarks</H1>
<DL><p>
{items}
</DL><p>
"""


def base_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Georgia, 'Times New Roman', serif; line-height: 1.58; margin: 0; color: #1d1d1d; background: #f7f4ed; }}
    main, .site-header {{ max-width: 880px; margin: 0 auto; padding: 24px; background: #fffdf8; }}
    .site-header {{ margin-top: 24px; border-bottom: 1px solid #ded7c8; font-family: Arial, sans-serif; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    h1 {{ font-size: clamp(1.8rem, 4vw, 2.8rem); }}
    a {{ color: #245f9b; }}
    img {{ max-width: 100%; height: auto; }}
    pre, code {{ font-family: Consolas, 'Courier New', monospace; }}
    pre {{ overflow-x: auto; padding: 12px; background: #f0eee8; }}
    .meta, .pager {{ color: #666; font-family: Arial, sans-serif; font-size: 0.92rem; }}
    .pager {{ margin: 18px 0; }}
    ol {{ padding-left: 1.4rem; }}
    li {{ margin: 0.35rem 0; }}
    time {{ display: inline-block; min-width: 6.5rem; color: #777; font-family: Arial, sans-serif; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def write_root_index(out_dir: Path, all_posts: dict[str, list[Post]]) -> None:
    links = []
    for site, posts in all_posts.items():
        links.append(f'<li><a href="{site}/index.html">{html.escape(SITES[site]["title"])}</a> ({len(posts)} posts, <a href="{site}/bookmarks.html">bookmarks</a>)</li>')
    (out_dir / "index.html").write_text(base_html("2000 Things Archive", f"<main><h1>2000 Things Archive</h1><ul>{''.join(links)}</ul></main>"), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", default=os.environ.get("ALL_PROXY", "socks5://127.0.0.1:8123"))
    parser.add_argument("--out", default="site")
    parser.add_argument("--cache", default="cache")
    args = parser.parse_args()

    out_dir = Path(args.out)
    cache_dir = Path(args.cache)
    all_posts: dict[str, list[Post]] = {}
    for site, config in SITES.items():
        host = config["host"]
        print(f"Fetching CDX for {host}...", flush=True)
        cdx_data = load_or_fetch(cache_dir / "cdx" / f"{site}.json", cdx_url(host), args.proxy)
        snapshots = parse_cdx(cdx_data)
        post_snaps = discover_post_urls(snapshots, host)
        print(f"{site}: {len(post_snaps)} post URLs found in CDX", flush=True)

        posts: list[Post] = []
        discovered_extra: set[str] = set()
        for original_url, snap in sorted(post_snaps.items()):
            post = fetch_post(site, host, original_url, snap, cache_dir, args.proxy)
            if post:
                posts.append(post)
                discovered_extra.update(discover_links_from_html(post.html, host))

        for original_url in sorted(discovered_extra - set(post_snaps)):
            snap = fetch_snapshot_for_url(original_url, cache_dir, args.proxy)
            if not snap:
                continue
            post = fetch_post(site, host, original_url, snap, cache_dir, args.proxy)
            if post:
                posts.append(post)

        unique: dict[str, Post] = {}
        for post in posts:
            unique.setdefault(post.original_url, post)
        posts = sorted(unique.values(), key=lambda p: (p.date, p.slug))
        print(f"{site}: {len(posts)} usable posts fetched", flush=True)
        write_site(site, config, posts, out_dir)
        all_posts[site] = posts

    write_root_index(out_dir, all_posts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
