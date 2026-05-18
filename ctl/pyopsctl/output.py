"""Output formatting for pyopsctl.

Three modes:
- Default (human): aligned columns via `format_table`. Mutating commands
  print one human line.
- `--json`: server payloads pass through verbatim for single-call
  commands; `emit_composite` wraps multi-call composites as
  `{"data": …, "meta": {...}}`. Errors go to stderr as
  `{"error": "...", "status": N}`.
- `--quiet`: IDs only on stdout, one per line; designed for shell piping.

Exit-code policy lives in `__main__.main()` and in client.HTTPError —
this module only formats.
"""
import json
import sys
from typing import Any, Iterable, Sequence


def emit(args, payload: Any, *, id_field: str | None = None,
         human: str | None = None, table: tuple[Sequence[str], Iterable[Sequence[Any]]] | None = None) -> None:
    """Emit a successful result in the mode requested by `args`.

    - `--quiet`: prints `payload[id_field]` (or each element's `[id_field]`
      if `payload` is a list) one per line. `id_field` must be set, else
      falls back to whatever `human` would print.
    - `--json`: prints `payload` as JSON to stdout.
    - default: prints `human` (a one-line message) if given, otherwise
      `table` (header row + body rows) if given, otherwise pretty JSON.
    """
    if getattr(args, "quiet", False) and id_field is not None:
        if isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict) and id_field in row:
                    print(row[id_field])
        elif isinstance(payload, dict) and id_field in payload:
            print(payload[id_field])
        return

    if getattr(args, "json_mode", False):
        json.dump(payload, sys.stdout, indent=None, default=str)
        sys.stdout.write("\n")
        return

    if human is not None:
        print(human)
        return

    if table is not None:
        headers, rows = table
        print(format_table(headers, rows))
        return

    print(json.dumps(payload, indent=2, default=str))


def emit_composite(args, data: Any, *, meta: dict | None = None,
                   human: str | None = None) -> None:
    """Emit a multi-call composite result. In `--json` mode wraps as
    `{"data": data, "meta": {...}}`; otherwise behaves like `emit` with
    the data alone.
    """
    if getattr(args, "json_mode", False):
        payload = {"data": data}
        if meta:
            payload["meta"] = meta
        json.dump(payload, sys.stdout, indent=None, default=str)
        sys.stdout.write("\n")
        return
    emit(args, data, human=human)


def emit_error(args, message: str, *, status: int | None = None) -> None:
    """Emit an error to stderr. JSON-mode wraps as a structured object;
    human mode prints a plain line."""
    if getattr(args, "json_mode", False):
        payload: dict[str, Any] = {"error": message}
        if status is not None:
            payload["status"] = status
        json.dump(payload, sys.stderr, default=str)
        sys.stderr.write("\n")
    else:
        if status is not None:
            print(f"pyopsctl: error ({status}): {message}", file=sys.stderr)
        else:
            print(f"pyopsctl: error: {message}", file=sys.stderr)


def emit_bytes(args, data: bytes, *, out_path: str | None) -> None:
    """Emit a binary blob (e.g. a job-workspace zip). `--out PATH` writes
    to file; absence writes to stdout (binary)."""
    if out_path:
        with open(out_path, "wb") as f:
            f.write(data)
        if not getattr(args, "quiet", False):
            print(f"wrote {len(data)} bytes to {out_path}")
    else:
        sys.stdout.buffer.write(data)


def format_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """Plain aligned-column table. No `rich` dep — stdlib only.
    Truncates nothing; the caller's responsibility to keep cells terse.
    """
    rows_list = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows_list:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for row in rows_list:
        padded = list(row) + [""] * (len(widths) - len(row))
        lines.append(fmt.format(*padded[:len(widths)]))
    return "\n".join(lines)
