# Editor Jedi Integration

app/routers/scriptlet/editor.py provides endpoints for in-browser editors and Jedi-based completions.

**Editor Serving**:
- GET /scriptlet/editor_task/library/view/md: Returns HTMLResponse from sites.get("scriptlet/editor_*") for task/library/view/markdown editors. Handles exceptions with 500/traceback logging. No params; static per type.

**Completions (POST /scriptlet/completion)**:
- Accepts CompletionArgument (Pydantic: code:str, line:int, column:int).
- jedi.settings.fast_parser=False (uses full parser for accuracy).
- Creates jedi.Script(code), calls .complete(line, column).
- Maps completion.type to score: module=1, class=2, path=3, keyword=4, property=5, statement=6, instance=7, function=8, param=9 (others=0).
- Returns list of dicts: {"name": completion.name, "value": name, "meta": completion.docstring(), "score": type_scores[type]}.
- Exceptions: 500 with traceback log.

**Client Integration (from editor_*.html search)**:
- Editors (task/library/view/md) fetch /inventory/code?set&name on load for initial code.
- POST /completion on Ace events (e.g., cursor move) for suggestions, integrated into Ace autocomplete UI.
- No server-side linting observed (docstring mentions "pylint" but implements completions only; potential gap or client-side).
- Run integration: PUT /job/item to enqueue (code/param/debug), poll /job/item?id for status (disable btn_run during running), cancel /job/cancel?id, stream logs via workspace files (implied).

Mechanisms: Server-side Jedi for precise completions (full parse, docstrings, typed scores for ranking), lightweight (no full IDE), but synchronous POST (potential latency), no caching/context (per-request Script), limited to Python (no multi-lang). Editors use popups (window.open no toolbar), Ace for syntax/highlight (themes via /files/css/ace/), draft handling (DELETE /inventory/draft on rollback).
