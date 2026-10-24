# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4", "markdownify"]
# ///
"""Mirror the UNSW Battlecode docs and rules pages into docs/unsw/ as markdown.

Run from anywhere:  uv run scripts/mirror_docs.py

Crawls every page under https://game.battlecode.au/docs, plus the rules pages (about, terms, tournaments).
The site only renders a code sample's Python tab on the server, so the C, C++ and Raw tabs are read out of each
page's JavaScript chunk, as are the full helper library sources. Animated board demos become their captions.
Warns about anything it could not match, and about pages in the site's search index that weren't written.
"""

from __future__ import annotations

import datetime
import argparse
import json
import os
import re
import subprocess
import urllib.request
from collections import deque
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify

GAME = "https://game.battlecode.au"
SITE = "https://battlecode.au"
OUT = Path(__file__).resolve().parent.parent / "docs" / "unsw"
TODAY = datetime.date.today().isoformat()
DO_NOT_EDIT = "Do not edit by hand; re-run scripts/mirror_docs.py instead."

# Code-tab keys in the page chunks: label shown above the block, and the fence language.
LANGS = {"python": ("Python", "python"), "c": ("C", "c"), "cpp": ("C++", "cpp"), "raw": ("Raw protocol", "text")}

# Helper sources embedded in the Helper Reference chunk, recognised by how they start.
HELPER_SOURCES = [
    ("import sys", "python", "helper.py", "Python", ""),
    ("#ifndef UNSWBC_HELPER_H", "c", "helper.h", "C",
     " The C tab documents the header only; the C implementation ships with the toolkit."),
    ("#pragma once", "cpp", "helper.hpp", "C++", ""),
]

SITE_PAGES = [
    (f"{SITE}/", "about.md"),
    (f"{SITE}/terms", "terms.md"),
    (f"{GAME}/tournaments", "tournaments.md"),
]

JS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "`": "`", "\\": "\\", "$": "$", "'": "'", '"': '"', "0": "\0"}


def fetch(url: str) -> str:
    if browser := os.environ.get("GSTACK_BROWSE"):
        expression = ("(async () => { const r = await fetch(" + json.dumps(url) + "); "
                      "if (!r.ok) throw new Error('HTTP ' + r.status); "
                      "return JSON.stringify(await r.text()); })()")
        result = subprocess.run([browser, "js", expression], capture_output=True, text=True,
                                check=True, timeout=60)
        return json.loads(result.stdout)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (unsw-battlecode docs mirror)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def read_template(js: str, start: int) -> tuple[str, int]:
    """Decode the JS template literal whose opening backtick is at js[start]; return it and the index after it."""
    out: list[str] = []
    i = start + 1
    while True:
        ch = js[i]
        if ch == "\\":
            nxt = js[i + 1]
            if nxt == "u" and js[i + 2] == "{":
                end = js.index("}", i)
                out.append(chr(int(js[i + 3:end], 16)))
                i = end + 1
            elif nxt == "u":
                out.append(chr(int(js[i + 2:i + 6], 16)))
                i += 6
            else:
                out.append(JS_ESCAPES.get(nxt, nxt))
                i += 2
        elif ch == "`":
            return "".join(out), i + 1
        elif ch == "$" and js[i + 1] == "{":
            depth, j = 0, i + 1
            while True:
                depth += {"{": 1, "}": -1}.get(js[j], 0)
                if depth == 0:
                    break
                j += 1
            out.append(js[i:j + 1])
            i = j + 1
        else:
            out.append(ch)
            i += 1


def snippet_objects(js: str) -> list[dict[str, str]]:
    """Every `{python:`...`,c:`...`,...}` code-tab object in a page chunk."""
    snippets = []
    for match in re.finditer(r"\{python:`", js):
        i, snippet = match.start() + 1, {}
        while key := re.match(r"([a-z]+):`", js[i:]):
            value, i = read_template(js, i + len(key.group(1)) + 1)
            snippet[key.group(1)] = value
            if js[i] != ",":
                break
            i += 1
        snippets.append(snippet)
    return snippets


def template_literals(js: str) -> list[str]:
    literals, i = [], 0
    while (i := js.find("`", i)) >= 0:
        try:
            value, i = read_template(js, i)
        except (IndexError, ValueError):
            i += 1
            continue
        literals.append(value)
    return literals


def node_chunk_urls(page_html: str) -> dict[int, str]:
    """Map SvelteKit route-node ids to their JS chunk URLs, via the app entry the page imports."""
    entry = re.search(r'import\("\.\./?(_app/immutable/entry/app\.[^"]+\.js)"\)', page_html)
    if entry is None:
        entry = re.search(r'import\("\./(_app/immutable/entry/app\.[^"]+\.js)"\)', page_html)
    app_js = fetch(f"{GAME}/{entry.group(1)}")
    return {int(node): f"{GAME}/_app/immutable/nodes/{node}.{name}"
            for node, name in re.findall(r"import\(`\.\./nodes/(\d+)\.([^`]+)`\)", app_js)}


def page_node_id(page_html: str) -> int:
    return int(re.search(r"node_ids: \[([0-9, ]+)\]", page_html).group(1).split(",")[-1])


def crawl_docs() -> dict[str, str]:
    """Every /docs/<slug> page reachable from the overview, as slug -> HTML. /docs itself is the overview."""
    pages: dict[str, str] = {}
    queue = deque(["overview"])
    while queue:
        slug = queue.popleft()
        if slug in pages:
            continue
        pages[slug] = fetch(f"{GAME}/docs/{slug}")
        for found in re.findall(r'href="/docs/([a-z0-9-]+)["#]', pages[slug]):
            if found not in pages:
                queue.append(found)
    return pages


def docs_nav(overview_html: str) -> list[tuple[str, list[tuple[str, str]]]]:
    groups = re.findall(r'<div class="docs-group[^"]*"><p[^>]*>(.*?)</p>(.*?)</div>', overview_html, re.S)
    return [(title, re.findall(r'href="/docs/([a-z0-9-]+)"[^>]*>([^<]+)</a>', links)) for title, links in groups]


def search_index_slugs(overview_html: str) -> set[str]:
    index = overview_html[overview_html.find("docsIndex:["):]
    return {slug or "overview" for slug in re.findall(r'href:"/docs/?([a-z0-9-]*)', index)}


def fence(code: str, lang: str = "") -> str:
    ticks = "```"
    while ticks in code:
        ticks += "`"
    return f"{ticks}{lang}\n{code.rstrip()}\n{ticks}"


def guess_lang(code: str) -> str:
    code = code.lstrip()
    if code.startswith("[") and "=" in code and "language" in code:
        return "toml"
    if re.match(r"(unswbc|uv |pip|curl|cd |python|mkdir|zip|export|git )", code):
        return "sh"
    if code.startswith(("{", "[")):
        return "json"
    return ""


def normalise(code: str) -> str:
    return "\n".join(line.rstrip() for line in code.strip("\n").splitlines())


def cf_decode(hex_string: str) -> str:
    """Undo Cloudflare's email obfuscation (it also mangles things like `zip=@file.zip`)."""
    data = bytes.fromhex(hex_string)
    return "".join(chr(byte ^ data[0]) for byte in data[1:])


def rewrite_href(href: str) -> str:
    if href.startswith("/cdn-cgi/l/email-protection#"):
        return "mailto:" + cf_decode(href.split("#", 1)[1])
    if doc := re.match(r"^/docs(?:/([a-z0-9-]+))?(#.*)?$", href):
        return f"{doc.group(1) or 'overview'}.md{doc.group(2) or ''}"
    return GAME + href if href.startswith("/") else href


def to_markdown(html: str) -> str:
    text = markdownify(html, heading_style="ATX", bullets="-", sub_symbol="<sub>", sup_symbol="<sup>",
                       escape_underscores=False, escape_asterisks=False)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def code_block(block, snippets: dict[str, dict[str, str]], slug: str) -> str:
    code = block.find("pre").get_text()
    if block.select_one('[role="tablist"]') is None:
        return fence(code, guess_lang(code))
    snippet = snippets.get(normalise(code))
    if snippet is None:
        tabs = [tab.get_text(strip=True) for tab in block.select('[role="tab"]')]
        print(f"WARN {slug}: no JS snippet matches a tabbed block; only its {tabs[0]} tab is mirrored")
        return f"**{tabs[0]}** (other tabs: {', '.join(tabs[1:])}; not recoverable)\n\n" + fence(code, "python")
    unknown = set(snippet) - set(LANGS)
    if unknown:
        print(f"WARN {slug}: unknown code tabs {sorted(unknown)}")
    parts = []
    for key, (label, lang) in LANGS.items():
        if key in snippet:
            parts.append(f"**{label}**\n\n" + fence(snippet[key], guess_lang(snippet[key]) or lang))
    return "\n\n".join(parts)


def convert_doc(slug: str, html: str, snippets: dict[str, dict[str, str]]) -> str:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    for comment in article.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for element in article.select("div.docs-turn") + article.find_all("svg"):
        element.decompose()
    for element in article.select(".__cf_email__[data-cfemail]"):
        element.replace_with(cf_decode(element["data-cfemail"]))

    # Blocks that markdownify would mangle are swapped for tokens and put back after conversion.
    blocks: dict[str, str] = {}

    def swap(element, markdown: str) -> None:
        token = f"MIRRORTOKEN{len(blocks)}X"
        blocks[token] = markdown
        placeholder = soup.new_tag("p")
        placeholder.string = token
        element.replace_with(placeholder)

    for figure in article.select("figure.clip"):
        caption = figure.select_one("figcaption .text")
        text = caption.get_text(" ", strip=True) if caption else ""
        swap(figure, f"> *[Animated board demo on the live page]* {text}".rstrip())
    for block in article.select("div.code-block"):
        swap(block, code_block(block, snippets, slug))
    for symbols in article.select("div.syms"):
        items = []
        for symbol in symbols.select(":scope > div.sym"):
            description = symbol.find("p")
            item = f"- `{symbol.find('code').get_text()}`"
            items.append(item + (f" — {description.get_text(' ', strip=True)}" if description else ""))
        swap(symbols, "\n".join(items))
    for tabs in article.select('div.tabs[role="tablist"]'):
        names = ", ".join(button.get_text(strip=True) for button in tabs.find_all("button"))
        swap(tabs, f"[Language tabs on the live page: {names}. Python is shown below; the full C and C++ helper "
                   "sources are in helper-source-c.md and helper-source-cpp.md.]")
    for link in article.find_all("a", href=True):
        link["href"] = rewrite_href(link["href"])

    text = to_markdown(str(article))
    for token, markdown in blocks.items():
        text = text.replace(token, markdown)
    return f"<!-- Mirrored from {GAME}/docs/{slug} on {TODAY}. {DO_NOT_EDIT} -->\n\n{text}"


def helper_source_pages(helper_js: str) -> dict[str, str]:
    pages = {}
    for source in (literal for literal in template_literals(helper_js) if len(literal) > 3000):
        for prefix, key, filename, label, note in HELPER_SOURCES:
            if source.lstrip().startswith(prefix):
                pages[f"helper-source-{key}.md"] = (
                    f"<!-- Extracted from the Helper Reference page bundle at {GAME}/docs/helper on {TODAY}. "
                    f"{DO_NOT_EDIT} -->\n\n# Helper source: `{filename}` ({label})\n\n"
                    f"The Helper Reference page renders its {label} tab from this source, which is the `{filename}` "
                    f"that `unswbc init` ships.{note}\n\n{fence(source, key)}\n")
    missing = {f"helper-source-{key}.md" for _, key, *_ in HELPER_SOURCES} - set(pages)
    if missing:
        print(f"WARN helper sources not found in the helper chunk: {sorted(missing)}")
    return pages


def convert_site_page(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.body
    for comment in root.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for element in root.find_all(["svg", "script", "style", "canvas", "img", "button", "audio", "video"]):
        element.decompose()
    for element in root.select(".__cf_email__[data-cfemail]"):
        element.replace_with(cf_decode(element["data-cfemail"]))
    origin = "/".join(url.split("/")[:3])
    for link in root.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/cdn-cgi/l/email-protection#"):
            link["href"] = "mailto:" + cf_decode(href.split("#", 1)[1])
        elif href.startswith("/"):
            link["href"] = origin + href
    text = to_markdown(str(root))
    return (f"<!-- Mirrored from {url} on {TODAY}. Not part of /docs: competition rules and schedule pages. "
            f"{DO_NOT_EDIT} -->\n\n{text}")


def readme(nav: list[tuple[str, list[tuple[str, str]]]]) -> str:
    lines = [
        f"<!-- Mirrored from {GAME}/docs on {TODAY} by scripts/mirror_docs.py. -->", "",
        "# UNSW Battlecode 2026 documentation (mirror)", "",
        f"Every page of {GAME}/docs, converted to markdown by `scripts/mirror_docs.py`. Code samples include every "
        "language tab (Python, C, C++, and Raw protocol where the site has one). Animated board demos can't be "
        "captured as text; each is replaced by its caption.", "",
    ]
    for title, links in nav:
        lines += [f"## {title}", "", *(f"- [{label}]({slug}.md)" for slug, label in links), ""]
    lines += [
        "## Extras", "",
        "- Helper library sources (what `unswbc init` ships): [Python](helper-source-python.md) · "
        "[C header](helper-source-c.md) · [C++](helper-source-cpp.md)", "",
        "## Competition rules and schedule (site pages, not under /docs)", "",
        f"- [About](site/about.md): eligibility, prizes, timeline, FAQ (including the Grand Final bracket), from {SITE}/",
        f"- [Terms & Conditions](site/terms.md): the rules (eligibility, accounts, teams, IP, fair play, prizes), "
        f"from {SITE}/terms",
        f"- [Tournaments](site/tournaments.md): Sprint / Qualifiers / Grand Final, from {GAME}/tournaments", "",
        "## Mirror notes", "",
        "- **Animated board demos** are canvas animations on the site. Each is replaced here by its caption, marked",
        "  *[Animated board demo on the live page]*.",
        "- **Hidden text** (checked 2026-09-23). The site's search index includes two passages that sit inside HTML",
        "  comments in the Movement page source and don't show on the page. Both are outdated, so they aren't",
        "  mirrored as page text:",
        "  - Sprinting: \"Any move with more steps than the dragon can pay for is not executed at all, and is treated",
        "    as an invalid action.\" The live rule (Death by Sprint) says the payable steps are taken and then the",
        "    dragon dies.",
        "  - Death by Sprint: \"This can be safely avoided by using the helper interface's `ct.make_moves()` function,",
        "    which safely returns without sending the move to the engine…\" The current `helper.py` `make_moves`",
        "    sends whatever it is given (see [helper-source-python.md](helper-source-python.md)).",
        "- **Completeness.** The script warns if a page in the site's search index wasn't written, or if a tabbed",
        "  code block had no matching JS snippet. This is a live-site snapshot, not a full Discord changelog.", "",
    ]
    return "\n".join(lines)


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-only", action="store_true", help="leave separate competition/rules pages unchanged")
    parser.add_argument("--out", type=Path, default=OUT, help="stage a mirror without touching the working copy")
    args = parser.parse_args()
    OUT = args.out
    pages = crawl_docs()
    overview = pages["overview"]
    chunks = node_chunk_urls(overview)
    node_js = {slug: fetch(chunks[page_node_id(html)]) for slug, html in pages.items()}
    snippets = {normalise(s["python"]): s for js in node_js.values() for s in snippet_objects(js) if "python" in s}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "site").mkdir(exist_ok=True)
    for slug, html in sorted(pages.items()):
        (OUT / f"{slug}.md").write_text(convert_doc(slug, html, snippets))
    for filename, text in helper_source_pages(node_js["helper"]).items():
        (OUT / filename).write_text(text)
    for url, filename in ([] if args.docs_only else SITE_PAGES):
        (OUT / "site" / filename).write_text(convert_site_page(fetch(url), url))
    (OUT / "README.md").write_text(readme(docs_nav(overview)))

    missing = search_index_slugs(overview) - set(pages)
    if missing:
        print(f"WARN pages in the site's search index that weren't mirrored: {sorted(missing)}")
    print(f"Mirrored {len(pages)} doc pages, 3 helper sources and {0 if args.docs_only else len(SITE_PAGES)} site pages into {OUT}")


if __name__ == "__main__":
    main()
