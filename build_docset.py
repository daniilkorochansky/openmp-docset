#!/usr/bin/env python3
"""Build an offline Dash/Zeal docset from the open.mp Docusaurus docs."""

from __future__ import annotations

import argparse
import hashlib
import html
import posixpath
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

try:
    import markdown
except ImportError:  # Keep link helpers and their tests usable without the renderer installed.
    markdown = None


DOCSET_NAME = "open.mp"
DOCSET_IDENTIFIER = "openmp"
DOCS_HOSTS = {"open.mp", "www.open.mp", "docs.open.mp"}
DOCS_ROUTE_PREFIXES = ("/docs/", "/docs")
IGNORED_DIRS = {".git", ".github", "__pycache__", "node_modules"}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}
MARKDOWN_SUFFIXES = {".md", ".mdx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico"}
MAX_EXTERNAL_IMAGE_BYTES = 20 * 1024 * 1024

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="openmp-docset">
<title>{title} - open.mp Docs</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff;
    --text: #24292f;
    --muted: #656d76;
    --code-bg: #f6f8fa;
    --border: #d0d7de;
    --link: #0969da;
    --note-bg: #eef6fc;
    --table-alt: #f6f8fa;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1b1b1d;
      --text: #e6edf3;
      --muted: #8b949e;
      --code-bg: #282a36;
      --border: #444c56;
      --link: #58a6ff;
      --note-bg: #1f2d3d;
      --table-alt: #22262b;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; scroll-padding-top: 16px; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.65;
    color: var(--text);
    background: var(--bg);
    max-width: 1040px;
    margin: 0 auto;
    padding: 24px 30px 56px;
    overflow-wrap: anywhere;
  }}
  h1, h2, h3, h4, h5, h6 {{ line-height: 1.3; margin-top: 1.6em; }}
  h1 {{ margin-top: 0; font-size: 2em; }}
  h1, h2, h3 {{ border-bottom: 1px solid var(--border); padding-bottom: .3em; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  a:target, :target {{ scroll-margin-top: 16px; }}
  p, ul, ol {{ margin: .8em 0; }}
  img {{ max-width: 100%; height: auto; }}
  pre {{
    background: var(--code-bg);
    padding: 14px 16px;
    border-radius: 7px;
    overflow-x: auto;
    border: 1px solid var(--border);
    line-height: 1.5;
  }}
  code, kbd {{
    font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    background: var(--code-bg);
    padding: 2px 5px;
    border-radius: 4px;
    font-size: .92em;
  }}
  pre code {{ padding: 0; background: transparent; border-radius: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 18px 0; display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid var(--border); padding: 8px 12px; text-align: left; }}
  th {{ background: var(--code-bg); }}
  tr:nth-child(even) {{ background: var(--table-alt); }}
  blockquote {{
    margin: 16px 0;
    padding: 8px 16px;
    border-left: 4px solid var(--link);
    background: var(--note-bg);
  }}
  hr {{ border: 0; border-top: 1px solid var(--border); margin: 28px 0; }}
  .docset-footer {{ color: var(--muted); font-size: .85em; border-top: 1px solid var(--border); margin-top: 48px; padding-top: 12px; }}
  .toc {{ border: 1px solid var(--border); border-radius: 7px; padding: 12px 18px; }}
</style>
</head>
<body>
<h1>{title}</h1>
{content}
<footer class="docset-footer">open.mp documentation</footer>
</body>
</html>
"""

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>openmp</string>
    <key>CFBundleName</key>
    <string>open.mp</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>DocSetPlatformFamily</key>
    <string>openmp</string>
    <key>isDashDocset</key>
    <true/>
    <key>dashIndexFilePath</key>
    <string>index.html</string>
    <key>DashDocSetDefaultFTSEnabled</key>
    <true/>
</dict>
</plist>
"""


@dataclass
class DocPage:
    source_path: Path
    relative_path: Path
    html_path: Path
    title: str
    metadata: dict[str, str] = field(default_factory=dict)
    headings: dict[str, str] = field(default_factory=dict)


class HTMLLinkRewriter(HTMLParser):
    """Rewrite links in rendered HTML without requiring an HTML DOM dependency."""

    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, page: DocPage, resolver: "PageResolver") -> None:
        super().__init__(convert_charrefs=False)
        self.page = page
        self.resolver = resolver
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rendered_attrs: list[str] = []
        for name, value in attrs:
            if value is None:
                rendered_attrs.append(name)
                continue
            if name.lower() in {"href", "src", "poster"}:
                value = self.resolver.rewrite_url(self.page, value, attribute=name.lower())
            rendered_attrs.append(f'{name}="{html.escape(value, quote=True)}"')
        self.parts.append(f"<{tag}{(' ' + ' '.join(rendered_attrs)) if rendered_attrs else ''}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rendered_attrs = []
        for name, value in attrs:
            if value is None:
                rendered_attrs.append(name)
                continue
            if name.lower() in {"href", "src", "poster"}:
                value = self.resolver.rewrite_url(self.page, value, attribute=name.lower())
            rendered_attrs.append(f'{name}="{html.escape(value, quote=True)}"')
        self.parts.append(f"<{tag}{(' ' + ' '.join(rendered_attrs)) if rendered_attrs else ''} />")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")

    def result(self) -> str:
        return "".join(self.parts)


class PageResolver:
    """Resolve Docusaurus routes and relative Markdown links to generated HTML pages."""

    def __init__(self, pages: Iterable[DocPage], source_dir: Path, docs_dir: Path) -> None:
        self.source_dir = source_dir
        self.docs_dir = docs_dir
        self.pages = list(pages)
        self.by_alias: dict[str, DocPage | None] = {}
        self.by_alias_lower: dict[str, DocPage | None] = {}
        self.by_title: dict[str, DocPage | None] = {}
        self.by_title_lower: dict[str, DocPage | None] = {}
        self.assets: set[str] = set()
        self.external_assets: dict[str, str] = {}
        self.external_asset_warnings: list[tuple[str, str]] = []
        self.unresolved: list[tuple[str, str]] = []
        for page in self.pages:
            for alias in self._page_aliases(page):
                self._register(alias, page)
            title = page.title.strip()
            if title:
                if title not in self.by_title:
                    self.by_title[title] = page
                elif self.by_title[title] != page:
                    self.by_title[title] = None
                lower_title = title.lower()
                if lower_title not in self.by_title_lower:
                    self.by_title_lower[lower_title] = page
                elif self.by_title_lower[lower_title] != page:
                    self.by_title_lower[lower_title] = None

        for path in source_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() not in MARKDOWN_SUFFIXES:
                self.assets.add(path.relative_to(source_dir).as_posix())

    @staticmethod
    def _normalise_key(path: str) -> str:
        path = unquote(path).replace("\\", "/")
        path = re.sub(r"/{2,}", "/", path)
        path = posixpath.normpath(path)
        if path == ".":
            path = ""
        return path.strip("/")

    def _register(self, alias: str, page: DocPage) -> None:
        key = self._normalise_key(alias)
        if not key:
            key = "index"
        if key not in self.by_alias:
            self.by_alias[key] = page
        elif self.by_alias[key] != page:
            # Do not silently route a colliding path to an arbitrary page.
            self.by_alias[key] = None
        lower = key.lower()
        if lower not in self.by_alias_lower:
            self.by_alias_lower[lower] = page
        elif self.by_alias_lower[lower] != page:
            self.by_alias_lower[lower] = None

    def _page_aliases(self, page: DocPage) -> set[str]:
        rel = page.relative_path.as_posix()
        no_suffix = str(Path(rel).with_suffix("" )).replace("\\", "/")
        aliases = {rel, no_suffix, no_suffix + ".html"}
        if no_suffix.endswith("/index"):
            aliases.add(no_suffix[:-6])
        if no_suffix == "index":
            aliases.update({"", "/", "docs", "docs/index"})
        slug = page.metadata.get("slug", "").strip()
        permalink = page.metadata.get("permalink", "").strip()
        doc_id = page.metadata.get("id", "").strip()
        for route in (slug, permalink, doc_id):
            if route:
                aliases.add(route)
                aliases.add(route.lstrip("/"))
                if route.startswith("/"):
                    aliases.add("docs" + route)
        # Docusaurus routes docs below /docs/ on open.mp. The docset keeps
        # the docs-relative route as well, so both URL styles work offline.
        aliases |= {"docs/" + alias.lstrip("/") for alias in list(aliases) if alias}
        return aliases

    def _lookup(self, path: str) -> DocPage | None:
        key = self._normalise_key(path)
        if not key:
            key = "index"
        if key in self.by_alias:
            return self.by_alias[key]
        return self.by_alias_lower.get(key.lower())

    def _candidate_paths(self, page: DocPage, raw_path: str) -> list[str]:
        path = unquote(raw_path).replace("\\", "/")
        candidates: list[str] = []
        if path.startswith("/"):
            absolute = path.lstrip("/")
            candidates.append(absolute)
            for prefix in DOCS_ROUTE_PREFIXES:
                if path.startswith(prefix):
                    candidates.append(path[len(prefix):].lstrip("/"))
            # Links sometimes use a docs-root path without the /docs prefix.
            candidates.append(absolute)
        else:
            candidates.append(posixpath.normpath(posixpath.join(page.relative_path.parent.as_posix(), path)))
            candidates.append(posixpath.normpath(path))
        expanded: list[str] = []
        for candidate in candidates:
            candidate = self._normalise_key(candidate)
            if not candidate:
                candidate = "index"
            expanded.append(candidate)
            suffix = Path(candidate).suffix.lower()
            if suffix in MARKDOWN_SUFFIXES:
                expanded.append(str(Path(candidate).with_suffix("")).replace("\\", "/"))
            elif suffix == ".html":
                expanded.append(str(Path(candidate).with_suffix("")).replace("\\", "/"))
            elif not suffix:
                expanded.extend((candidate + ".md", candidate + ".mdx", candidate + "/index.md", candidate + "/index.mdx"))
        # Remove duplicates without changing resolution preference.
        return list(dict.fromkeys(expanded))

    def resolve_page(self, page: DocPage, raw_path: str) -> DocPage | None:
        for candidate in self._candidate_paths(page, raw_path):
            target = self._lookup(candidate)
            if target is not None:
                return target
        return None

    def resolve_title(self, title: str) -> DocPage | None:
        title = unquote(title).strip()
        return self.by_title.get(title) or self.by_title_lower.get(title.lower())

    def _relative_page_url(self, source: DocPage, target: DocPage, query: str = "", fragment: str = "") -> str:
        relative = posixpath.relpath(target.html_path.as_posix(), source.html_path.parent.as_posix() or ".")
        if relative == ".":
            relative = target.html_path.name
        if relative == target.html_path.name and target == source:
            relative = ""
        return urlunsplit(("", "", relative, query, self._rewrite_fragment(target, fragment)))

    def _download_external_image(self, page: DocPage, raw_url: str) -> str | None:
        """Mirror external Markdown images so the docset remains useful offline."""
        url = "https:" + raw_url if raw_url.startswith("//") else raw_url
        cached = self.external_assets.get(url)
        if cached:
            return posixpath.relpath(cached, page.html_path.parent.as_posix() or ".")

        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return None
        suffix = Path(unquote(parts.path)).suffix.lower()
        is_known_image = suffix in IMAGE_SUFFIXES
        # Avoid downloading arbitrary HTML/JSON sources from <iframe>, <source>, etc.
        if not is_known_image:
            return None

        request_url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        try:
            request = Request(request_url, headers={"User-Agent": "openmp-docset/1.0"})
            with urlopen(request, timeout=20) as response:
                headers = getattr(response, "headers", None)
                content_type = headers.get_content_type() if headers and hasattr(headers, "get_content_type") else ""
                if content_type and not content_type.startswith("image/"):
                    raise ValueError(f"unexpected content type: {content_type}")
                content = response.read(MAX_EXTERNAL_IMAGE_BYTES + 1)
                if len(content) > MAX_EXTERNAL_IMAGE_BYTES:
                    raise ValueError("image exceeds the 20 MiB download limit")
                if not content:
                    raise ValueError("empty image response")

            digest = hashlib.sha256(request_url.encode("utf-8")).hexdigest()[:16]
            asset_rel = Path("_external") / f"{digest}{suffix}"
            destination = self.docs_dir / asset_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            self.external_assets[url] = asset_rel.as_posix()
            return posixpath.relpath(asset_rel.as_posix(), page.html_path.parent.as_posix() or ".")
        except Exception as exc:
            self.external_asset_warnings.append((page.relative_path.as_posix(), f"{url} ({exc})"))
            return None

    def rewrite_url(self, page: DocPage, raw_url: str, attribute: str = "href") -> str:
        raw_url = html.unescape(raw_url.strip())
        if not raw_url:
            return raw_url
        if raw_url.startswith("#"):
            return "#" + self._rewrite_fragment(page, raw_url[1:])
        parts = urlsplit(raw_url)
        scheme = parts.scheme.lower()
        if scheme == "docset-symbol":
            target = self.resolve_title(parts.path)
            if target is not None:
                return self._relative_page_url(page, target, parts.query, parts.fragment)
            self.unresolved.append((page.relative_path.as_posix(), raw_url))
            return "#"
        if scheme or raw_url.startswith("//"):
            is_openmp_docs = scheme in {"http", "https"} and (parts.hostname or "").lower() in DOCS_HOSTS
            if not is_openmp_docs:
                if attribute in {"src", "poster"}:
                    downloaded = self._download_external_image(page, raw_url)
                    if downloaded:
                        return downloaded
                return raw_url
        if not parts.path:
            return raw_url

        target = self.resolve_page(page, parts.path)
        if target is not None:
            return self._relative_page_url(page, target, parts.query, parts.fragment)

        # Preserve regular external links and relative links to copied assets. For
        # root-relative assets, make the path relative to the current document.
        path_key = self._normalise_key(parts.path)
        asset_key = path_key if parts.path.startswith("/") else self._normalise_key(
            posixpath.join(page.relative_path.parent.as_posix(), parts.path)
        )
        if asset_key in self.assets:
            asset_rel = posixpath.relpath(asset_key, page.html_path.parent.as_posix() or ".")
            return urlunsplit(("", "", asset_rel, parts.query, parts.fragment))
        if attribute in {"src", "poster"} and scheme in {"http", "https"}:
            downloaded = self._download_external_image(page, raw_url)
            if downloaded:
                return downloaded
        if attribute == "href" and self._looks_like_doc_link(parts.path):
            self.unresolved.append((page.relative_path.as_posix(), raw_url))
        return raw_url

    @staticmethod
    def _looks_like_doc_link(path: str) -> bool:
        lower = path.lower()
        if lower.endswith((".md", ".mdx", ".html")) or lower.startswith("/docs/"):
            return True
        # Docusaurus page routes usually have no file extension.
        return bool(path and path not in {"/", "./", "../"} and not Path(path).suffix)

    @staticmethod
    def _rewrite_fragment(target: DocPage, fragment: str) -> str:
        if not fragment:
            return ""
        decoded = unquote(fragment)
        return target.headings.get(decoded, target.headings.get(decoded.lower(), fragment))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the simple scalar/list frontmatter fields used by the docs."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
    if closing is None:
        return {}, text

    metadata: dict[str, str] = {}
    current_list_key: str | None = None
    list_values: list[str] = []
    for line in lines[1:closing]:
        if line.startswith((" ", "\t")) and current_list_key:
            item = line.strip()
            if item.startswith("-"):
                list_values.append(item[1:].strip().strip("\"'"))
            continue
        if current_list_key:
            metadata[current_list_key] = ",".join(list_values)
            current_list_key = None
            list_values = []
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value in {"", "[", "|-", ">-", ">"}:
            if value == "":
                current_list_key = key
            else:
                metadata[key] = ""
        elif value.startswith("[") and value.endswith("]"):
            metadata[key] = ",".join(x.strip().strip("\"'") for x in value[1:-1].split(",") if x.strip())
        else:
            metadata[key] = value.strip("\"'")
    if current_list_key:
        metadata[current_list_key] = ",".join(list_values)
    body = "\n".join(lines[closing + 1:]).lstrip("\n")
    return metadata, body


def clean_docusaurus_syntax(md_text: str) -> str:
    """Remove/translate common Docusaurus MDX constructs while keeping prose."""
    result: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0
    admonition_depth = 0
    skip_multiline_import = False
    void_html_tags = {"img", "br", "hr", "input", "source", "video", "audio", "embed", "wbr"}

    for original_line in md_text.splitlines():
        line = original_line
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if in_fence:
            result.append(f"> {line}" if admonition_depth else line)
            if fence_match and fence_match.group(1)[0] == fence_char and len(fence_match.group(1)) >= fence_length:
                in_fence = False
            continue
        if fence_match:
            result.append(f"> {line}" if admonition_depth else line)
            fence_char = fence_match.group(1)[0]
            fence_length = len(fence_match.group(1))
            in_fence = True
            continue

        # Imports/exports are MDX build-time dependencies, not documentation.
        if skip_multiline_import:
            if re.search(r"\bfrom\s+[\"']|;\s*$", line):
                skip_multiline_import = False
            continue
        if re.match(r"^\s*import\s+", line):
            if not re.search(r"\bfrom\s+[\"']|;\s*$|^\s*import\s+[\"']", line):
                skip_multiline_import = True
            continue
        if re.match(r"^\s*export\s+", line) or re.match(r"^\s*//", line):
            continue

        opening = re.match(r"^\s*:::(note|tip|info|warning|danger|caution)\s*(.*)$", line, re.I)
        if opening:
            kind, title = opening.groups()
            label = f"**{kind.capitalize()}**"
            if title:
                label += f" — {title.strip()}"
            result.append(f"> {label}")
            admonition_depth += 1
            continue
        if re.match(r"^\s*:::\s*$", line):
            admonition_depth = max(0, admonition_depth - 1)
            result.append("")
            continue

        # Tab content remains in the generated page, with a heading to distinguish
        # each variant instead of relying on the interactive Docusaurus widget.
        line = re.sub(r"<Tabs\b[^>]*>", "", line, flags=re.I)
        line = re.sub(r"</Tabs\s*>", "", line, flags=re.I)
        line = re.sub(
            r'<TabItem\b[^>]*(?:label|value)=["\']([^"\']+)["\'][^>]*>',
            lambda m: f"### {m.group(1)}",
            line,
            flags=re.I,
        )
        line = re.sub(r"</TabItem\s*>", "", line, flags=re.I)

        # Docusaurus <Link to="...">label</Link> becomes a regular local anchor.
        line = re.sub(
            r'<Link\b[^>]*(?:to|href)=["\']([^"\']+)["\'][^>]*>(.*?)</Link\s*>',
            lambda m: f'<a href="{html.escape(m.group(1), quote=True)}">{m.group(2)}</a>',
            line,
            flags=re.I,
        )

        # Remove self-closing React widgets but retain valid HTML void elements,
        # notably <img /> which is common in documentation.
        def strip_self_closing(match: re.Match[str]) -> str:
            tag_match = re.match(r"<\s*([A-Za-z][\w:-]*)", match.group(0))
            if not tag_match:
                return ""
            tag_name = tag_match.group(1)
            if tag_name.lower() in void_html_tags:
                return match.group(0)
            if tag_name == "NoteNPCCallbacks":
                return "> **Tip**\n> This callback can also be called by NPC."
            return ""

        line = re.sub(r"<\w+\b[^>]*\/>", strip_self_closing, line)
        for component in ("Admonition", "Steps", "Step", "Details"):
            line = re.sub(rf"</?{component}\b[^>]*>", "", line, flags=re.I)

        # Resolve Docusaurus inline symbol references (commonly function and
        # callback names) in the HTML link-rewrite pass after every page is known.
        def convert_doc_reference(match: re.Match[str]) -> str:
            target = match.group(1).strip()
            label = (match.group(2) or target).strip()
            if "/" in target or target.startswith(".") or target.lower().endswith((".md", ".mdx")):
                href = target
            else:
                href = "docset-symbol:" + quote(target, safe="")
            return f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'

        line = re.sub(r"\{@link\s+([^}|]+)(?:\|([^}]+))?\}", convert_doc_reference, line)

        if admonition_depth:
            result.append(">" if not line.strip() else f"> {line}")
        else:
            result.append(line)

    return "\n".join(result)


def github_slugify(value: str, separator: str = "-") -> str:
    """Slug headings like Docusaurus/GitHub so cross-page fragments stay stable."""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s]+", separator, value)
    return value


def _extract_heading_map(html_body: str) -> dict[str, str]:
    """Map GitHub/Docusaurus heading fragments to the IDs emitted by Markdown."""
    class HeadingParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.current_id: str | None = None
            self.current_text: list[str] = []
            self.result: dict[str, str] = {}
            self.slug_counts: dict[str, int] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if re.fullmatch(r"h[1-6]", tag, re.I):
                self.current_id = dict(attrs).get("id")
                self.current_text = []

        def handle_data(self, data: str) -> None:
            if self.current_id is not None:
                self.current_text.append(data)

        def handle_endtag(self, tag: str) -> None:
            if self.current_id is not None and re.fullmatch(r"h[1-6]", tag, re.I):
                text_value = " ".join("".join(self.current_text).split())
                slug = github_slugify(text_value)
                if slug:
                    occurrence = self.slug_counts.get(slug, 0)
                    self.slug_counts[slug] = occurrence + 1
                    unique_slug = slug if occurrence == 0 else f"{slug}-{occurrence}"
                    self.result.setdefault(slug, self.current_id)
                    self.result.setdefault(slug.lower(), self.current_id)
                    self.result[unique_slug] = self.current_id
                    self.result[unique_slug.lower()] = self.current_id
                self.current_id = None
                self.current_text = []

    parser = HeadingParser()
    parser.feed(html_body)
    return parser.result


def detect_entry_type(rel_path: str, title: str, metadata: dict[str, str] | None = None) -> str:
    """Return a useful, consistent Dash/Zeal search category."""
    parts = {part.lower() for part in Path(rel_path).parts}
    title_lower = title.lower()
    if "callbacks" in parts or title.startswith("On"):
        return "Callback"
    if "functions" in parts or "natives" in parts:
        return "Function"
    if "enumerations" in parts or "enums" in parts:
        return "Enum"
    if "constants" in parts or "defines" in parts or "resources" in parts:
        return "Constant"
    if "tutorials" in parts or "guides" in parts:
        return "Guide"
    if "tags" in parts or title_lower.endswith(" tag"):
        return "Tag"
    return "Section"


def _load_markdown_renderer():
    if markdown is None:
        raise RuntimeError(
            "The 'Markdown' package is required. Install dependencies with: pip install -r requirements.txt"
        )
    return markdown.Markdown(
        extensions=["fenced_code", "tables", "codehilite", "toc", "sane_lists", "attr_list", "md_in_html"],
        extension_configs={"toc": {"slugify": github_slugify, "permalink": True}},
        output_format="html5",
    )


def _collect_pages(source_dir: Path) -> list[DocPage]:
    pages: list[DocPage] = []
    for file_path in sorted(source_dir.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in MARKDOWN_SUFFIXES:
            continue
        if any(part in IGNORED_DIRS for part in file_path.relative_to(source_dir).parts):
            continue
        if file_path.name.startswith("_"):
            continue
        rel_path = file_path.relative_to(source_dir)
        raw_text = file_path.read_text(encoding="utf-8-sig", errors="replace")
        metadata, body = parse_frontmatter(raw_text)
        title = metadata.get("title", "").strip()
        if not title:
            h1 = re.search(r"^#\s+(.+?)\s*#*\s*$", body, flags=re.MULTILINE)
            title = h1.group(1).strip() if h1 else file_path.stem
        body = re.sub(rf"^#\s+{re.escape(title)}\s*#*\s*\n+", "", body, count=1, flags=re.MULTILINE)
        html_path = rel_path.with_suffix(".html")
        pages.append(DocPage(file_path, rel_path, html_path, title, metadata))
    return pages


def _copy_assets(source_dir: Path, docs_dir: Path) -> int:
    copied = 0
    for file_path in source_dir.rglob("*"):
        if not file_path.is_file():
            continue
        rel_path = file_path.relative_to(source_dir)
        if file_path.suffix.lower() in MARKDOWN_SUFFIXES or file_path.name in IGNORED_FILES:
            continue
        if any(part in IGNORED_DIRS for part in rel_path.parts):
            continue
        target = docs_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
        copied += 1
    return copied


def _render_markdown_body(page: DocPage, md_converter) -> str:
    raw_text = page.source_path.read_text(encoding="utf-8-sig", errors="replace")
    _metadata, md_body = parse_frontmatter(raw_text)
    md_body = re.sub(
        rf"^#\s+{re.escape(page.title)}\s*#*\s*\n+",
        "",
        md_body,
        count=1,
        flags=re.MULTILINE,
    )
    md_body = clean_docusaurus_syntax(md_body)
    md_converter.reset()
    html_body = md_converter.convert(md_body)
    page.headings = _extract_heading_map(html_body)
    return html_body


def _write_html_page(page: DocPage, html_body: str, docs_dir: Path, resolver: PageResolver) -> None:
    rewriter = HTMLLinkRewriter(page, resolver)
    rewriter.feed(html_body)
    rewriter.close()
    final_html = HTML_TEMPLATE.format(
        title=html.escape(page.title),
        content=rewriter.result(),
    )
    target = docs_dir / page.html_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(final_html, encoding="utf-8")


def _write_fallback_index(pages: list[DocPage], docs_dir: Path) -> None:
    if (docs_dir / "index.html").exists():
        return
    sections = ["<p>Offline reference for open.mp server development.</p>", "<ul>"]
    for page in pages:
        if page.html_path.as_posix() == "index.html":
            continue
        link = page.html_path.as_posix()
        sections.append(f'<li><a href="{html.escape(link, quote=True)}">{html.escape(page.title)}</a></li>')
    sections.append("</ul>")
    (docs_dir / "index.html").write_text(
        HTML_TEMPLATE.format(title=DOCSET_NAME, content="\n".join(sections)),
        encoding="utf-8",
    )


def build_docset(source_dir: str | Path, output_dir: str | Path = "openmp.docset") -> dict[str, int]:
    src_path = Path(source_dir).resolve()
    if not src_path.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {src_path}")

    docset_path = Path(output_dir)
    if docset_path.exists():
        if docset_path.is_dir():
            shutil.rmtree(docset_path)
        else:
            docset_path.unlink()

    contents_dir = docset_path / "Contents"
    resources_dir = contents_dir / "Resources"
    docs_dir = resources_dir / "Documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (contents_dir / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")

    pages = _collect_pages(src_path)
    if not pages:
        raise RuntimeError(f"No Markdown documents found in {src_path}")
    resolver = PageResolver(pages, src_path, docs_dir)
    copied_assets = _copy_assets(src_path, docs_dir)
    md_converter = _load_markdown_renderer()

    db_path = resources_dir / "docSet.dsidx"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, path TEXT NOT NULL)"
        )
        cursor.execute("CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path)")

        indexed_count = 0
        # First render every page and collect its heading IDs. This makes links to
        # headings on pages rendered later resolve correctly in the second pass.
        rendered_pages = {page.relative_path.as_posix(): _render_markdown_body(page, md_converter) for page in pages}
        for page in pages:
            _write_html_page(page, rendered_pages[page.relative_path.as_posix()], docs_dir, resolver)
            entry_type = detect_entry_type(page.relative_path.as_posix(), page.title, page.metadata)
            cursor.execute(
                "INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?, ?, ?)",
                (page.title, entry_type, page.html_path.as_posix()),
            )
            indexed_count += max(cursor.rowcount, 0)

        conn.commit()

    _write_fallback_index(pages, docs_dir)

    # Check for invalid documentation links after every page has been rendered.
    unresolved = sorted(set(resolver.unresolved))
    if unresolved:
        print(f"Warning: {len(unresolved)} unresolved internal documentation link(s):", file=sys.stderr)
        for source, target in unresolved[:50]:
            print(f"  {source}: {target}", file=sys.stderr)
        if len(unresolved) > 50:
            print(f"  ... and {len(unresolved) - 50} more", file=sys.stderr)
    if resolver.external_asset_warnings:
        print(f"Warning: {len(resolver.external_asset_warnings)} external image(s) could not be mirrored:", file=sys.stderr)
        for source, target in resolver.external_asset_warnings[:25]:
            print(f"  {source}: {target}", file=sys.stderr)

    result = {
        "pages": len(pages),
        "indexed": indexed_count,
        "assets": copied_assets + len(resolver.external_assets),
        "external_images": len(resolver.external_assets),
        "unresolved_links": len(unresolved),
    }
    print(
        "Built {output}: {pages} pages, {indexed} indexed entries, {assets} assets, "
        "{unresolved_links} unresolved internal links.".format(output=docset_path, **result)
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the open.mp Dash/Zeal docset.")
    parser.add_argument("source", nargs="?", default="upstream/frontend/docs", help="Source docs directory")
    parser.add_argument("--output", default="openmp.docset", help="Output .docset directory")
    args = parser.parse_args(argv)
    try:
        build_docset(args.source, args.output)
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
