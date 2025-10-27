# Current Authentication Mechanisms

Authentication uses X-User header in requests (get_current_user dependency in routers), defaults to anonymous. Owner in commit msgs only. Scriptlets exec in isolated venvs/workspaces (data/jobs/{owner}/{set}/{id}). ZMQ IPC local (ipc://). Git ops per-set, logs access/info.

app/routers/scriptlet/*: Depends on get_current_user (header or anonymous).
app/lib/git.py: Commits use user from header in messages.
No password/JWT/token validation, sessions, or role-based access; scriptlet execution via exec(). Potential for header spoofing in non-HTTPS.
