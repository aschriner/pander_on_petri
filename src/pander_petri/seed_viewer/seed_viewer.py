"""Browse and edit Bloom scenario seeds in a local browser table.

Every ``**/scenarios/seeds/*.md`` under the root (``behaviors/`` by default)
becomes one row. Columns are the seed's frontmatter keys and tagged blocks, with
tag attributes split out as ``<tag>_<attr>`` (``proposition_id``) and a
``stance`` attribute kept as the value's stance. A fully scripted ``<followups>``
block becomes one column per ``<turnN>``. Edits are written back into the seed
file in place, touching only the edited value::

    uv run pander-seeds                 # http://127.0.0.1:8765
    uv run pander-seeds behaviors/escalate_deescalate --port 9000

The server binds to localhost and reads and writes only seed files under the root.
"""

from __future__ import annotations

import argparse
import json
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from pander_petri.bloom import _ATTR, _TAG, _TURN, SeedFormatError, parse_seed

_FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?\n)---\n", re.DOTALL)
_FRONTMATTER_LINE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):[ \t]*(?P<value>.*)$", re.MULTILINE)


class SeedEditError(ValueError):
    """An edit names no field, or would change the seed's fields."""


def seed_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("**/scenarios/seeds") if p.is_dir())


def fields(text: str) -> list[dict]:
    """Every editable value in a seed, in file order: ``key``, ``stance`` (or
    None), ``kind`` (frontmatter, attr, or tag), and its ``start``/``end`` in ``text``."""
    out: list[dict] = []
    if frontmatter := _FRONTMATTER.match(text):
        base = frontmatter.start("body")
        for m in _FRONTMATTER_LINE.finditer(frontmatter["body"]):
            out.append(dict(key=m["key"], stance=None, kind="frontmatter",
                            start=base + m.start("value"), end=base + m.end("value")))
    for m in _TAG.finditer(text):
        tag, body_start, stance = m["tag"], m.start("body"), None
        for a in _ATTR.finditer(m["attrs"]):
            if a[1] == "stance":
                stance = a[2]
            else:
                out.append(dict(key=f"{tag}_{a[1]}", stance=None, kind="attr",
                                start=m.start("attrs") + a.start(2), end=m.start("attrs") + a.end(2)))
        turns = list(_TURN.finditer(m["body"]))
        # a fully scripted <followups> is one column per turn; a mixed one stays whole
        if tag == "followups" and turns and not _TURN.sub("", m["body"]).strip():
            for t in turns:
                out.append(dict(key=f"turn{t['turn']}", stance=dict(_ATTR.findall(t["attrs"])).get("stance"),
                                kind="tag", start=body_start + t.start("body"), end=body_start + t.end("body")))
            continue
        out.append(dict(key=tag, stance=stance, kind="tag", start=body_start, end=m.end("body")))
    return out


def _shape(fs: list[dict]) -> list[tuple]:
    return [(f["key"], f["stance"], f["kind"]) for f in fs]


def edit(text: str, key: str, stance: str | None, index: int, value: str) -> str:
    """``text`` with the ``index``-th ``(key, stance)`` value replaced by ``value``.

    Whitespace around a tag body is kept as written. Raises SeedEditError if
    there's no such field, or if the new value would add, drop, or reshape fields
    (e.g. it contains a stray ``</opening>``).
    """
    before = fields(text)
    matches = [f for f in before if f["key"] == key and f["stance"] == stance]
    if index >= len(matches):
        raise SeedEditError(f"no field {key!r} (stance {stance!r}) #{index}")
    field = matches[index]
    if field["kind"] != "tag" and "\n" in value.strip():
        raise SeedEditError(f"{key} must be a single line")
    if field["kind"] == "attr" and '"' in value:
        raise SeedEditError(f'{key} can\'t contain "')
    old = text[field["start"]:field["end"]]
    if field["kind"] == "tag":
        value = old[: len(old) - len(old.lstrip())] + value.strip() + old[len(old.rstrip()):]
    else:
        value = value.strip()
    new_text = text[: field["start"]] + value + text[field["end"]:]
    if _shape(fields(new_text)) != _shape(before):
        raise SeedEditError("that value would change the seed's tags (a stray <tag> or </tag>?)")
    return new_text


def validate(text: str) -> str | None:
    """Why ``parse_seed`` rejects this seed, or None."""
    try:
        parse_seed(text)
    except SeedFormatError as e:
        return str(e)
    return None


def describe(root: Path, path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    behavior = path.parent.parent.parent.relative_to(root).as_posix()  # <behavior>/scenarios/seeds/x.md
    return dict(
        id=path.relative_to(root).as_posix(),
        behavior=root.name if behavior == "." else behavior,
        file=path.stem,
        mtime=path.stat().st_mtime_ns,
        fields=[dict(key=f["key"], stance=f["stance"], kind=f["kind"],
                     value=text[f["start"]:f["end"]].strip()) for f in fields(text)],
        error=validate(text),
    )


def load_all(root: Path) -> list[dict]:
    return [describe(root, p) for d in seed_dirs(root) for p in sorted(d.glob("*.md"))]


def _resolve(root: Path, seed_id: str) -> Path:
    path = (root / seed_id).resolve()
    if path.suffix != ".md" or not path.is_file() or path.parent not in {d.resolve() for d in seed_dirs(root)}:
        raise SeedEditError(f"not a seed under {root}: {seed_id}")
    return path


def make_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    page = files("pander_petri").joinpath("seed_viewer.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: object) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/seeds":
                self._json(200, dict(root=str(root), seeds=load_all(root)))
            else:
                self._json(404, dict(error="not found"))

        def do_POST(self) -> None:
            if self.path != "/api/save":
                return self._json(404, dict(error="not found"))
            # a page on another origin can POST here too; only our own page may write
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self._json(403, dict(error="cross-origin write refused"))
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            try:
                path = _resolve(root, req["id"])
                if path.stat().st_mtime_ns != req["mtime"]:
                    return self._json(409, dict(error="the file changed on disk; reloaded it, edit again",
                                                seed=describe(root, path)))
                text = path.read_text(encoding="utf-8")
                path.write_text(edit(text, req["key"], req.get("stance"), req.get("index", 0), req["value"]),
                                encoding="utf-8")
            except SeedEditError as e:
                return self._json(400, dict(error=str(e)))
            self._json(200, dict(seed=describe(root, path)))

        def log_message(self, format: str, *args: object) -> None:
            if self.command == "POST":
                super().log_message(format, *args)

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Browse and edit Bloom scenario seeds in a local browser table.")
    parser.add_argument("root", nargs="?", default="behaviors", type=Path,
                        help="directory to scan for **/scenarios/seeds (default: behaviors)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not seed_dirs(root):
        parser.error(f"no scenarios/seeds directories under {root}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(root))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Serving seeds under {root} at {url} (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
