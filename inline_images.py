#!/usr/bin/env python3
"""Inline external <img src="..."> references as base64 data URLs.

Downloads are cached under cache/images so the script can be safely re-run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse


IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\ssrc=)(["\'])(https?://[^"\']+)(\2)', re.I | re.S)
WAYBACK_RE = re.compile(r"^https?://web\.archive\.org/web/(\d{14})(?:[a-z_]+)?/(https?://.+)$", re.I)


def run_curl(url: str, proxy: str, timeout: int, connect_timeout: int, retries: int, retry_delay: int) -> bytes:
    cmd = [
        "curl.exe",
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        str(retries),
        "--retry-all-errors",
        "--retry-delay",
        str(retry_delay),
        "--connect-timeout",
        str(connect_timeout),
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


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.lstrip().startswith(b"<svg") or b"<svg" in data[:512].lower():
        return "image/svg+xml"
    return None


def cache_name(url: str, mime: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    ext = mimetypes.guess_extension(mime.split(";", 1)[0]) or ""
    if ext == ".jpe":
        ext = ".jpg"
    if not ext:
        path_ext = Path(urlparse(unquote(html.unescape(url))).path).suffix.lower()
        ext = path_ext if re.match(r"^\.[a-z0-9]{1,5}$", path_ext) else ".bin"
    return digest + ext


def load_manifest(path: Path) -> dict[str, dict[str, str | int | bool]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, dict[str, str | int | bool]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def image_candidates(url: str) -> list[str]:
    original_url = html.unescape(url)
    candidates = [original_url]
    match = WAYBACK_RE.match(original_url)
    if match:
        timestamp, archived = match.group(1), html.unescape(match.group(2))
        candidates.append(archived)
        parsed = urlparse(archived)
        no_query = parsed._replace(query="").geturl()
        if no_query != archived:
            candidates.append(no_query)
            candidates.append(f"https://web.archive.org/web/{timestamp}id_/{no_query}")
    else:
        parsed = urlparse(original_url)
        no_query = parsed._replace(query="").geturl()
        if no_query != original_url:
            candidates.append(no_query)
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def fetch_image(
    url: str,
    cache_dir: Path,
    manifest: dict[str, dict[str, str | int | bool]],
    proxy: str,
    delay: float,
    retry_failed: bool,
    timeout: int,
    connect_timeout: int,
    retries: int,
    retry_delay: int,
) -> str | None:
    original_url = html.unescape(url)
    item = manifest.get(original_url)
    if item and item.get("ok") and item.get("file"):
        image_path = cache_dir / str(item["file"])
        if image_path.exists() and image_path.stat().st_size > 0:
            data = image_path.read_bytes()
            mime = detect_image_mime(data)
            if mime:
                return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
            item["ok"] = False
            item["error"] = "cached file is not a recognized image"

    if item and item.get("ok") is False and not retry_failed:
        return None

    errors = []
    data = None
    fetched_url = original_url
    for candidate in image_candidates(original_url):
        try:
            if delay:
                time.sleep(delay)
            data = run_curl(candidate, proxy, timeout, connect_timeout, retries, retry_delay)
            mime = detect_image_mime(data)
            if not mime:
                errors.append(candidate + " -> not a recognized image")
                data = None
                continue
            fetched_url = candidate
            break
        except subprocess.CalledProcessError as exc:
            errors.append(candidate + " -> " + exc.output.decode("utf-8", errors="replace")[:220])
    if data is None:
        manifest[original_url] = {
            "ok": False,
            "error": " | ".join(errors)[:1000],
            "candidates": image_candidates(original_url),
        }
        return None

    mime = detect_image_mime(data)
    if not mime:
        manifest[original_url] = {
            "ok": False,
            "error": "not a recognized image",
            "bytes": len(data),
            "fetched_url": fetched_url,
        }
        return None

    name = cache_name(fetched_url, mime)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / name).write_bytes(data)
    manifest[original_url] = {
        "ok": True,
        "file": name,
        "mime": mime,
        "bytes": len(data),
        "fetched_url": fetched_url,
    }
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def inline_file(
    path: Path,
    cache_dir: Path,
    manifest: dict[str, dict[str, str | int | bool]],
    proxy: str,
    delay: float,
    retry_failed: bool,
    timeout: int,
    connect_timeout: int,
    retries: int,
    retry_delay: int,
) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    changed = 0
    failed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed, failed
        prefix, quote_char, url, end_quote = match.group(1), match.group(2), match.group(3), match.group(4)
        data_url = fetch_image(url, cache_dir, manifest, proxy, delay, retry_failed, timeout, connect_timeout, retries, retry_delay)
        if data_url is None:
            failed += 1
            return match.group(0)
        changed += 1
        return prefix + quote_char + data_url + end_quote

    new_text = IMG_SRC_RE.sub(replace, text)
    if changed:
        path.write_text(new_text, encoding="utf-8")
    return changed, failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="site")
    parser.add_argument("--cache", default="cache/images")
    parser.add_argument("--manifest", default="cache/images/manifest.json")
    parser.add_argument("--proxy", default=os.environ.get("ALL_PROXY", "socks5://127.0.0.1:8123"))
    parser.add_argument("--delay", type=float, default=0.02)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--connect-timeout", type=int, default=8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-delay", type=int, default=1)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    site_dir = Path(args.site)
    cache_dir = Path(args.cache)
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    total_changed = 0
    total_failed = 0
    files_changed = 0
    html_files = sorted(site_dir.rglob("*.html"))
    for index, path in enumerate(html_files, 1):
        changed, failed = inline_file(
            path,
            cache_dir,
            manifest,
            args.proxy,
            args.delay,
            args.retry_failed,
            args.timeout,
            args.connect_timeout,
            args.retries,
            args.retry_delay,
        )
        if changed:
            files_changed += 1
        total_changed += changed
        total_failed += failed
        if index % 100 == 0:
            save_manifest(manifest_path, manifest)
            print(f"Processed {index}/{len(html_files)} files; inlined {total_changed}, failed {total_failed}", flush=True)

    save_manifest(manifest_path, manifest)
    print(f"Files changed: {files_changed}")
    print(f"Images inlined: {total_changed}")
    print(f"Images failed: {total_failed}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
