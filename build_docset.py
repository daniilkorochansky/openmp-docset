import os
import re
import sys
import shutil
import sqlite3
from pathlib import Path
import markdown

# Минималистичный и чистый CSS, адаптированный под светлую и темную темы
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - open.mp Docs</title>
<style>
  :root {{
    --bg: #ffffff;
    --text: #1c1e21;
    --code-bg: #f6f8fa;
    --border: #d0d7de;
    --link: #0969da;
    --note-bg: #eef6fc;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1b1b1d;
      --text: #e3e3e3;
      --code-bg: #282a36;
      --border: #44475a;
      --link: #58a6ff;
      --note-bg: #1f2d3d;
    }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.6;
    color: var(--text);
    background: var(--bg);
    max-width: 900px;
    margin: 0 auto;
    padding: 20px 28px;
  }}
  h1, h2, h3 {{ border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  pre {{
    background: var(--code-bg);
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    border: 1px solid var(--border);
  }}
  code {{
    font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
    background: var(--code-bg);
    padding: 2px 5px;
    border-radius: 4px;
    font-size: 0.92em;
  }}
  pre code {{ padding: 0; background: none; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 8px 12px;
    text-align: left;
  }}
  th {{ background: var(--code-bg); }}
  blockquote {{
    margin: 16px 0;
    padding: 8px 16px;
    border-left: 4px solid var(--link);
    background: var(--note-bg);
  }}
</style>
</head>
<body>
<h1>{title}</h1>
{content}
</body>
</html>
"""

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://apple.com">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>openmp</string>
    <key>CFBundleName</key>
    <string>open.mp</string>
    <key>DocSetPlatformFamily</key>
    <string>openmp</string>
    <key>isDashDocset</key>
    <true/>
    <key>dashIndexFilePath</key>
    <string>index.html</string>
</dict>
</plist>
"""


def parse_frontmatter(text: str):
    """Извлекает YAML frontmatter (--- ... ---) и возвращает (meta_dict, clean_markdown)."""
    meta = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1]
            text = parts[2].lstrip()
            for line in raw_meta.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text


def clean_docusaurus_syntax(md_text: str) -> str:
    """Очищает специфичные для Docusaurus директивы (:::note, import и т.д.) и правит ссылки."""
    # Убираем импорты React-компонентов (import ... from ...)
    md_text = re.sub(r"^import\s+.*?;\s*$", "", md_text, flags=re.MULTILINE)
    # Заменяем :::tip / :::warning / :::note на цитаты Markdown
    md_text = re.sub(r"^:::(note|tip|info|warning|danger|caution)\s*(.*)$", r"> **\1** \2\n>", md_text, flags=re.MULTILINE)
    md_text = re.sub(r"^:::\s*$", "", md_text, flags=re.MULTILINE)
    # Меняем относительные ссылки .md / .mdx на .html
    md_text = re.sub(r"\(([^)]+?)\.mdx?(#[^)]*)?\)", r"(\1.html\2)", md_text)
    return md_text


def detect_entry_type(rel_path: str, title: str) -> str:
    """Определяет категорию для поискового индекса Dash/Zeal."""
    p = rel_path.lower().replace("\\", "/")
    if "/callbacks/" in p or title.startswith("On"):
        return "Callback"
    if "/functions/" in p:
        return "Function"
    if "/resources/" in p:
        return "Constant"
    if "/tutorials/" in p:
        return "Guide"
    return "Section"


def build_docset(source_dir: str, output_dir: str = "openmp.docset"):
    src_path = Path(source_dir).resolve()
    if not src_path.exists():
        print(f"Error: Source directory {src_path} does not exist.")
        sys.exit(1)

    docset_path = Path(output_dir)
    if docset_path.exists():
        shutil.rmtree(docset_path)

    contents_dir = docset_path / "Contents"
    resources_dir = contents_dir / "Resources"
    docs_dir = resources_dir / "Documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Записываем Info.plist
    (contents_dir / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")

    # 2. Создаем SQLite базу docSet.dsidx
    db_path = resources_dir / "docSet.dsidx"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);")
    cursor.execute("CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);")

    md_converter = markdown.Markdown(
        extensions=["fenced_code", "tables", "codehilite", "toc", "sane_lists"]
    )

    indexed_count = 0

    # 3. Обходим все .md и .mdx файлы
    for root, _, files in os.walk(src_path):
        for file in files:
            file_path = Path(root) / file
            rel_path = file_path.relative_to(src_path)

            # Пропускаем служебные файлы Docusaurus
            if file.startswith("_"):
                continue

            # Копируем статику (картинки и т.д.), если она есть
            if file_path.suffix.lower() not in (".md", ".mdx"):
                dest_asset = docs_dir / rel_path
                dest_asset.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest_asset)
                continue

            raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
            meta, md_body = parse_frontmatter(raw_text)

            # Определяем заголовок (имя функции/раздела)
            title = meta.get("title")
            if not title:
                h1_match = re.search(r"^#\s+(.+)$", md_body, flags=re.MULTILINE)
                title = h1_match.group(1).strip() if h1_match else file_path.stem

            # Убираем дублирующийся H1 из тела, если он совпадает с title
            md_body = re.sub(rf"^#\s+{re.escape(title)}\s*\n+", "", md_body, count=1)
            md_body = clean_docusaurus_syntax(md_body)

            md_converter.reset()
            html_body = md_converter.convert(md_body)

            # Сохраняем HTML файл
            rel_html_path = rel_path.with_suffix(".html")
            dest_html = docs_dir / rel_html_path
            dest_html.parent.mkdir(parents=True, exist_ok=True)
            dest_html.write_text(
                HTML_TEMPLATE.format(title=title, content=html_body),
                encoding="utf-8"
            )

            # Добавляем запись в индекс SQLite
            entry_type = detect_entry_type(str(rel_path), title)
            posix_rel_html = rel_html_path.as_posix()

            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?, ?, ?)",
                    (title, entry_type, posix_rel_html)
                )
                indexed_count += 1
            except sqlite3.Error as e:
                print(f"DB error for {title}: {e}")

    conn.commit()
    conn.close()
    print(f"Successfully built {output_dir} with {indexed_count} indexed entries.")


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "upstream/frontend/docs"
    build_docset(source)
