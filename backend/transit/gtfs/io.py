"""Read and write GTFS tables as plain lists of dicts (stdlib csv).

Keeping tables as ``list[dict[str, str]]`` (all values strings, exactly as in
the files) makes the validator report problems faithfully. Type conversion
happens later in the graph/timetable builders.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Iterable, Mapping

Table = list[dict[str, str]]
Feed = dict[str, Table]   # filename -> rows


def read_table(path: Path) -> Table:
    # utf-8-sig strips an optional BOM, which GTFS allows.
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k is not None}
                for row in csv.DictReader(fh)]


def read_feed(source: Path) -> Feed:
    """Read every ``*.txt`` table from a feed directory or ``.zip``."""
    source = Path(source)
    feed: Feed = {}
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            for name in zf.namelist():
                if name.endswith(".txt") and "/" not in name:
                    text = zf.read(name).decode("utf-8-sig")
                    feed[name] = [{k.strip(): (v or "").strip() for k, v in r.items() if k is not None}
                                  for r in csv.DictReader(io.StringIO(text))]
    else:
        for p in sorted(source.glob("*.txt")):
            feed[p.name] = read_table(p)
    return feed


def header_of(path: Path) -> list[str]:
    """Column names of a table file (empty list if the file is empty)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [c.strip() for c in next(csv.reader(fh), [])]


def write_table(path: Path, fieldnames: list[str], rows: Iterable[Mapping[str, object]]) -> int:
    """Write rows with a fixed column order; omitted columns become empty.

    Uses ``\\n`` line endings and UTF-8 without BOM, as recommended by the
    GTFS best practices. Returns the number of rows written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
            n += 1
    return n


def zip_feed(feed_dir: Path, zip_path: Path) -> None:
    """Package all ``.txt`` files of a feed directory into a GTFS zip."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(Path(feed_dir).glob("*.txt")):
            zf.write(p, arcname=p.name)
