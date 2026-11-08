"""
World‑Anvil JSON → Markdown converter.

The script walks the *categories* directory, builds a folder hierarchy that mirrors
the category tree, and writes each article as a Markdown file into the matching
folder.  Only the ``content`` field of an article is kept – World‑Anvil uses a
tiny subset of markup which we normalise to plain text.

Typical usage::

    python world_anvil_converter.py --source input --output output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Configuration – can be overridden via command line.
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_DIR = Path.cwd() / "input"
DEFAULT_OUTPUT_DIR = Path.cwd() / "output"

# ---------------------------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Return a Windows‑friendly filename.

    Replaces the characters ``<>:"/\|?*`` with an underscore, strips
    surrounding whitespace and truncates to 255 characters (NTFS limit).
    """
    sanitized = re.sub(r'[<>:\"/\\\\|?*]', "_", name)
    return sanitized.strip()[:255]

@dataclass
class Category:
    id: str
    title: str
    parent_id: Optional[str] = None

# ---------------------------------------------------------------------------
def clean_content(raw: Optional[str]) -> str:
    """Convert World‑Anvil markup to plain Markdown.

    The exported JSON uses a very small subset of tags – ``[p]``, ``[/p]`` and
    list markers.  They are replaced with newlines so the resulting text is
    readable as simple Markdown.
    """
    if not raw:
        return ""
    cleaned = re.sub(r"\[/?(p|ol|ul|li)\]", "\n", raw)
    # Collapse runs of blank lines into a single paragraph break
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

# ---------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    """Load a JSON file from *path* and return the parsed dictionary."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
def build_category_map(source_dir: Path, output_dir: Path) -> Dict[str, Path]:
    """Return a mapping from category id to the folder ``Path``.

    The function walks all JSON files in ``source_dir/categories``, creates the
    corresponding directory under ``output_dir`` and returns a dictionary that
    maps each category id to its absolute path.  Paths are built by following
    parent links – this keeps the logic simple while still mirroring the tree.
    """
    cat_dir = source_dir / "categories"
    categories: Dict[str, Category] = {}
    for file in cat_dir.glob("*.json"):
        data = load_json(file)
        parent_obj = data.get("parent") or {}
        parent_id = parent_obj.get("id") if isinstance(parent_obj, dict) else None
        categories[data["id"]] = Category(
            id=data["id"], title=data.get("title", "Untitled"), parent_id=parent_id
        )

    # Memoisation cache for already‑computed paths.
    path_cache: Dict[str, Path] = {}

    def get_path(cat_id: str) -> Path:
        if cat_id in path_cache:
            return path_cache[cat_id]
        cat = categories.get(cat_id)
        if not cat:
            # Should never happen – fall back to the output root.
            path_cache[cat_id] = output_dir
            return output_dir
        parent_path = get_path(cat.parent_id) if cat.parent_id else output_dir
        path = parent_path / sanitize_filename(cat.title)
        path.mkdir(parents=True, exist_ok=True)
        path_cache[cat_id] = path
        return path

    # Build the mapping by resolving each category's full path.
    cat_map: Dict[str, Path] = {}
    for cid in categories:
        cat_map[cid] = get_path(cid)

    return cat_map
# ---------------------------------------------------------------------------
def process_articles(source_dir: Path, cat_map: Dict[str, Path]):
    """Process all articles and write them into a folder hierarchy.

    The algorithm now performs the following steps:

    1. Load every article JSON file.
    2. Build two mappings:
       * ``article_parent`` – maps an article ID to its parent ID (or ``None``).
       * ``children_map``   – reverse mapping of parent → list(child ids).
    3. Resolve the output directory for each article using a depth‑first
       traversal that respects the parent/child hierarchy.

    This guarantees that an article with a parent is placed in the same
    folder as its parent, and if it has children a subfolder named after the
    article is created.  Top‑level articles (no parent) are written directly
    into their category folder.
    """

    articles_dir = source_dir / "articles"
    article_map: Dict[str, dict] = {}
    article_parent: Dict[str, Optional[str]] = {}
    children_map: Dict[str, list[str]] = defaultdict(list)

    for file in articles_dir.glob("*.json"):
        data = load_json(file)
        aid = data.get("id")
        if not aid:
            continue
        article_map[aid] = data
        # Primary parent via ``articleParent`` field.
        pid_obj = data.get("articleParent") or {}
        pid = pid_obj.get("id") if isinstance(pid_obj, dict) else None
        article_parent[aid] = pid
        if pid:
            children_map[pid].append(aid)

    # Some articles only appear in a ``childrenArticles`` array.  Infer the
    # relationship for those.
    for data in article_map.values():
        cid = data.get("id")
        if not cid:
            continue
        children = data.get("childrenArticles") or []
        if isinstance(children, list):
            for child in children:
                child_id = child.get("id")
                if child_id and child_id not in article_parent:
                    article_parent[child_id] = cid
                    children_map[cid].append(child_id)

    # Find the category ID for "Cities / Villages" if present.  This is used
    # as a fallback when an article has no explicit ``category`` field but
    # represents a settlement or landmark that should live under this
    # category.
    city_villages_cat_id: Optional[str] = None
    cat_dir = source_dir / "categories"
    for file in cat_dir.glob("*.json"):
        data = load_json(file)
        if data.get("title") == "Cities / Villages":
            city_villages_cat_id = data["id"]
            break

    # Memoise output directories to avoid recomputing.
    dir_cache: Dict[str, Path] = {}

    def get_dir(aid: str) -> Path:
        if aid in dir_cache:
            return dir_cache[aid]
        data = article_map.get(aid)
        if not data:
            path = OUTPUT_DIR / f"{aid}_missing"
            dir_cache[aid] = path
            return path
        pid = article_parent.get(aid)
        title = data.get("title", "Unnamed")

        # Determine base directory.
        if not pid or pid == aid:
            # If the article has no parent (or erroneously lists itself as
            # parent) we use its own category.  Some settlement articles do
            # not include a ``category`` field; in that case fall back to
            # the root output directory.
            cat_obj = data.get("category") or {}
            cid = cat_obj.get("id")
            if not cid:
                # No category – place at the root of the output tree.
                path = OUTPUT_DIR
            else:
                base_dir = cat_map.get(cid, OUTPUT_DIR)
                path = base_dir
        else:
            path = get_dir(pid)

        # If the article has children, create a subfolder for it.
        if aid in children_map and children_map[aid]:
            path = path / sanitize_filename(title)
        dir_cache[aid] = path
        return path

    # Write all articles.
    for aid, data in article_map.items():
        out_dir = get_dir(aid)
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = out_dir / f"{sanitize_filename(data.get('title', 'Unnamed'))}.md"
        with filename.open("w", encoding="utf-8") as f:
            f.write(f"# {data.get('title','Untitled')}\n\n{clean_content(data.get('content',''))}")

        # This section was part of the old implementation and is no longer
        # needed.  It has been removed to avoid NameError due to undefined
        # variables.

# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert World Anvil JSON export to Markdown")
    parser.add_argument("--source", "-s", type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f"Source directory containing the exported data (default: {DEFAULT_SOURCE_DIR})")
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory for the converted Markdown files (default: {DEFAULT_OUTPUT_DIR})")
    return parser.parse_args(argv)

# ---------------------------------------------------------------------------
def main() -> int:
    args = parse_args()
    global SOURCE_DIR, OUTPUT_DIR
    SOURCE_DIR, OUTPUT_DIR = args.source, args.output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cat_map = build_category_map(SOURCE_DIR, OUTPUT_DIR)
    print(f"Created {len(cat_map)} category folders")
    process_articles(SOURCE_DIR, cat_map)
    print("Conversion complete.")
    return 0

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())
