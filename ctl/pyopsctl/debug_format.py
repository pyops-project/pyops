"""Human-readable rendering for `pyopsctl debug` results.

The default output for callstack-changing commands (`attach`, `step`,
`step-in`, `step-out`, `continue` when stopped, `stack`, `state`) is a
vertical "state dump" with four sections separated by 60-hyphen
rules:

    globals:
    - name: value
    - ...
    ------------------------------------------------------------
    locals:
    - name: value
    ------------------------------------------------------------
    code (test::scr_e2e):
       4  def my_func(x, y):
       5      z = x + y
     → 6      print(z)
       7      return z
    ------------------------------------------------------------
    callstack:
      0  test::scr_e2e:6 in my_func
      1  test::scr_e2e:10 in <module>
      2  <runner shim>:4 in <module>

Per-section helpers below are pure formatters — they take dicts from
the daemon's `--json` shape and emit strings. The CLI calls `render`
to pick the right composite based on the command.
"""
from typing import Any, Iterable

SEPARATOR = "-" * 60
VALUE_TRUNCATE = 120
ARROW = "→"


def _truncate(text: str, *, limit: int = VALUE_TRUNCATE) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_variables(title: str, variables: Iterable[dict]) -> str:
    """`globals:` / `locals:` section."""
    items = list(variables)
    if not items:
        return f"{title}:\n- (empty)"
    lines = [f"{title}:"]
    for v in items:
        name = v.get("name", "")
        value = _truncate(v.get("value", "") or "")
        type_ = v.get("type") or ""
        suffix = f"  # {type_}" if type_ else ""
        lines.append(f"- {name}: {value}{suffix}")
    return "\n".join(lines)


def render_code(code: dict | None) -> str:
    """`code (...):` section with `→` on the current line."""
    if not code:
        return "code:\n- (no source available)"
    set_name = code.get("set") or "?"
    name = code.get("name") or "?"
    current = code.get("current_line")
    lines = code.get("lines") or []
    if not lines:
        return f"code ({set_name}::{name}):\n- (empty)"
    width = max(len(str(line.get("n", 0))) for line in lines)
    out = [f"code ({set_name}::{name}):"]
    for line in lines:
        n = line.get("n")
        prefix = ARROW if line.get("current") else " "
        out.append(f" {prefix} {str(n).rjust(width)}  {line.get('text', '')}")
    return "\n".join(out)


def render_callstack(stack: Iterable[dict]) -> str:
    items = list(stack)
    if not items:
        return "callstack:\n- (empty)"
    width = max(len(str(i)) for i in range(len(items)))
    lines = ["callstack:"]
    for i, frame in enumerate(items):
        src = frame.get("source") or {}
        set_name = src.get("set")
        scriptlet_name = src.get("name")
        line = frame.get("line")
        func = frame.get("name") or "<module>"
        if set_name and scriptlet_name:
            location = f"{set_name}::{scriptlet_name}:{line}"
        elif src.get("is_shim"):
            location = f"<runner shim>:{line}"
        else:
            path = src.get("path") or "?"
            location = f"{path}:{line}"
        lines.append(f"  {str(i).rjust(width)}  {location} in {func}")
    return "\n".join(lines)


def render_state_dump(snapshot: dict) -> str:
    """The full `globals / locals / code / callstack` composite — the
    default output for attach, step, step-in, step-out, continue
    (when stopped), stack, and state.
    """
    if snapshot.get("state") == "terminated":
        return "terminated"
    sections = [
        render_variables("globals", snapshot.get("globals") or []),
        render_variables("locals", snapshot.get("locals") or []),
        render_code(snapshot.get("code")),
        render_callstack(snapshot.get("stack") or []),
    ]
    return f"\n{SEPARATOR}\n".join(sections)


def render_evaluate(result: dict) -> str:
    expr = result.get("expression", "")
    value = _truncate(result.get("value", "") or "")
    type_ = result.get("type") or ""
    suffix = f"  # {type_}" if type_ else ""
    return f"{expr}  =  {value}{suffix}"


def render_breakpoint(record: dict) -> str:
    """One line: `<id>  [verified|pending]  <set>::<name>:<line>  [if <cond>]`."""
    status = "verified" if record.get("verified") else (
        "pending" if record.get("pending") else "unverified")
    friendly = record.get("friendly") or "?"
    line = record.get("line")
    cond = record.get("condition")
    cond_part = f"  if {cond}" if cond else ""
    return f"{record.get('id', '?')}  [{status}]  {friendly}:{line}{cond_part}"


def render_breakpoints(records: Iterable[dict]) -> str:
    items = list(records)
    if not items:
        return "(no breakpoints)"
    return "\n".join(render_breakpoint(b) for b in items)


def render_session_list(sessions: Iterable[dict]) -> str:
    items = list(sessions)
    if not items:
        return "(no active debug sessions)"
    headers = ["job_id", "set", "name", "pdb_port", "pid", "attached_at"]
    rows = [
        [
            s.get("job_id", ""),
            s.get("set", ""),
            s.get("name", ""),
            str(s.get("pdb_port", "")),
            str(s.get("pid", "")),
            str(s.get("attached_at", "")),
        ]
        for s in items
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join([fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
                     + [fmt.format(*r) for r in rows])


def render_source(result: dict) -> str:
    set_name = result.get("set", "?")
    name = result.get("name", "?")
    lines = result.get("lines") or []
    if not lines:
        return f"source ({set_name}::{name}):\n(empty)"
    width = max(len(str(line.get("n", 0))) for line in lines)
    out = [f"source ({set_name}::{name}):"]
    for line in lines:
        out.append(f"  {str(line.get('n')).rjust(width)}  {line.get('text', '')}")
    return "\n".join(out)
