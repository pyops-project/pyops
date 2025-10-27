# Current UI Execution Mechanisms

app/web.py implements dynamic UI via controller/view scriptlets. controller_* endpoints exec Python code (from git.read_content) in glob={"request": Request, "set": str, "name": str}, capture stdout as HTMLResponse. view_* exec code for raw HTML. menu() queries DB for dynamic nav.
