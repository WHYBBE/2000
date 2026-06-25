#!/usr/bin/env python3
"""Normalize and highlight archived WordPress code blocks in generated HTML."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


PRE_RE = re.compile(r"(?:<p>)?\s*<pre\b([^>]*)>(.*?)</pre>\s*(?:</p>)?", re.I | re.S)
BRUSH_RE = re.compile(r"brush:\s*([a-z0-9_#+-]+)", re.I)

CODE_CSS = """
    .code-block { background: #101820; color: #e6edf3; border-radius: 10px; border: 1px solid #263545; box-shadow: inset 0 1px 0 rgba(255,255,255,.04); font-size: .94rem; line-height: 1.48; margin: 1.25rem 0; padding: 0; overflow: auto; tab-size: 4; }
    .code-block code { display: block; padding: 16px 18px; white-space: pre; font-family: Consolas, 'Cascadia Code', 'Courier New', monospace; }
    .code-block .kw { color: #ffcb6b; font-weight: 600; }
    .code-block .type { color: #82aaff; }
    .code-block .str { color: #c3e88d; }
    .code-block .num { color: #f78c6c; }
    .code-block .com { color: #7f8c98; font-style: italic; }
    .code-block .xml-tag { color: #89ddff; }
    .code-block .xml-name { color: #ffcb6b; }
    .code-block .xml-attr { color: #c792ea; }
    .code-block .xml-val { color: #c3e88d; }
"""

CSHARP_KEYWORDS = {
    "abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char", "checked",
    "class", "const", "continue", "decimal", "default", "delegate", "do", "double", "else",
    "enum", "event", "explicit", "extern", "false", "finally", "fixed", "float", "for",
    "foreach", "goto", "if", "implicit", "in", "int", "interface", "internal", "is", "lock",
    "long", "namespace", "new", "null", "object", "operator", "out", "override", "params",
    "private", "protected", "public", "readonly", "ref", "return", "sbyte", "sealed", "short",
    "sizeof", "stackalloc", "static", "string", "struct", "switch", "this", "throw", "true",
    "try", "typeof", "uint", "ulong", "unchecked", "unsafe", "ushort", "using", "var", "virtual",
    "void", "volatile", "while", "get", "set", "value", "where", "yield", "async", "await",
}

PS_KEYWORDS = {
    "begin", "break", "catch", "class", "continue", "data", "do", "dynamicparam", "else", "elseif",
    "end", "exit", "filter", "finally", "for", "foreach", "from", "function", "if", "in", "param",
    "process", "return", "switch", "throw", "trap", "try", "until", "using", "var", "while",
}


def language_from_attrs(attrs: str) -> str:
    match = BRUSH_RE.search(html.unescape(attrs))
    if not match:
        return "text"
    lang = match.group(1).lower()
    if lang in {"c#", "cs", "csharp"}:
        return "csharp"
    if lang in {"xml", "xaml", "html"}:
        return "xml"
    if lang in {"powershell", "ps", "ps1"}:
        return "powershell"
    return re.sub(r"[^a-z0-9_-]+", "", lang) or "text"


def normalize_code(fragment: str) -> str:
    text = html.unescape(fragment).replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    nonblank = sum(1 for line in lines if line.strip())
    blank = len(lines) - nonblank
    if nonblank and blank >= nonblank // 2:
        lines = [line for line in lines if line.strip()]
    min_indent = None
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        min_indent = indent if min_indent is None else min(min_indent, indent)
    if min_indent:
        lines = [line[min_indent:] if len(line) >= min_indent else line for line in lines]
    return "\n".join(lines)


def protect(pattern: str, text: str, cls: str, prefix: str, flags: int = 0) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        key = f"@@{prefix}{len(replacements)}@@"
        replacements[key] = f'<span class="{cls}">{match.group(0)}</span>'
        return key

    return re.sub(pattern, repl, text, flags=flags), replacements


def restore(text: str, replacements: dict[str, str]) -> str:
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def highlight_csharp(code: str) -> str:
    escaped = html.escape(code, quote=False)
    escaped, comments = protect(r"//[^\n]*|/\*.*?\*/", escaped, "com", "COM", re.S)
    escaped, strings = protect(r'@?"(?:""|\\.|[^"\n])*"|\'(?:\\.|[^\\\'])\'', escaped, "str", "STR")
    escaped = re.sub(r"\b\d+(?:\.\d+)?\b", r'<span class="num">\g<0></span>', escaped)
    escaped = re.sub(r"\b(" + "|".join(sorted(CSHARP_KEYWORDS)) + r")\b", r'<span class="kw">\g<1></span>', escaped)
    escaped = restore(escaped, strings)
    escaped = restore(escaped, comments)
    return escaped


def highlight_powershell(code: str) -> str:
    escaped = html.escape(code, quote=False)
    escaped, comments = protect(r"#[^\n]*", escaped, "com", "COM")
    escaped, strings = protect(r'".*?"|\'.*?\'', escaped, "str", "STR")
    escaped = re.sub(r"\b(" + "|".join(sorted(PS_KEYWORDS)) + r")\b", r'<span class="kw">\g<1></span>', escaped, flags=re.I)
    escaped = restore(escaped, strings)
    escaped = restore(escaped, comments)
    return escaped


def highlight_xml(code: str) -> str:
    escaped = html.escape(code, quote=False)

    def tag_repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        match_name = re.match(r"(&lt;/?)([A-Za-z_][\w:.-]*)(.*?)(/?&gt;)$", tag)
        if not match_name:
            return tag
        start, name, attrs, end = match_name.groups()
        attrs = re.sub(r'\s([A-Za-z_][\w:.-]*)(=)(".*?"|\'.*?\')', r' <span class="xml-attr">\1</span>\2<span class="xml-val">\3</span>', attrs)
        return f'<span class="xml-tag">{start}</span><span class="xml-name">{name}</span>{attrs}<span class="xml-tag">{end}</span>'

    return re.sub(r"&lt;/?[A-Za-z_][^\n&]*(?:&gt;|/&gt;)", tag_repl, escaped)


def highlight(code: str, lang: str) -> str:
    if lang == "csharp":
        return highlight_csharp(code)
    if lang == "xml":
        return highlight_xml(code)
    if lang == "powershell":
        return highlight_powershell(code)
    return html.escape(code, quote=False)


def enhance_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    if "code-block" in text:
        return 0
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        lang = language_from_attrs(match.group(1))
        code = normalize_code(match.group(2))
        if not code.strip():
            return match.group(0)
        count += 1
        highlighted = highlight(code, lang)
        return f'<pre class="code-block language-{lang}"><code>{highlighted}</code></pre>'

    new_text = PRE_RE.sub(repl, text)
    if count:
        new_text = inject_css(new_text)
        path.write_text(new_text, encoding="utf-8")
    return count


def inject_css(text: str) -> str:
    if ".code-block" in text:
        return text
    return text.replace("  </style>", CODE_CSS + "  </style>")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="site")
    args = parser.parse_args()
    total = 0
    files = 0
    for path in sorted(Path(args.site).rglob("*.html")):
        count = enhance_file(path)
        if count:
            files += 1
            total += count
    print(f"Files changed: {files}")
    print(f"Code blocks enhanced: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
