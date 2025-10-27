# Current Daemon Polling Mechanisms

app/runner.py daemon polls DB for jobs, spawns processes in venvs, handles signals via GracefulKiller. Key functions: getNextToDo, loadJob, run (exec code/libs/param), finishJob, schedule (croniter/always-on).
