# Error Handling Mechanisms

PyOps uses basic Python logging and HTTPExceptions for errors, with file-based logs from conf.

**Current Mechanisms**:
- **Logging**: logging.getLogger(__name__) in modules (runner.py, web.py, routers); levels from conf.log.loglevel ("debug"), handlers to conf.log.access/info (file). No rotation (manual or logrotate). Runner.py logs job states, exceptions; web.py logs requests/exceptions; routers raise HTTPException (404/409/500) with detail/traceback.
- **Error Handling**: In runner.py, try/except in run() captures traceback to queue/detail; signals for shutdown. Routers: try/except around ops (e.g., git.write_content), return 500 with traceback. Editor.py: 500 on Jedi errors. No global middleware; exec(code) in runner.py catches Exception, sets "failed" with str(e).
- **Tracebacks**: Logged to files, no structured format; console in dev.
