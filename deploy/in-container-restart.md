# Restart services inside the running container

After deploying this version and starting it once, use the container terminal in
`/app`:

```sh
python scripts/run_deployment.py restart all
python scripts/run_deployment.py restart rasa
python scripts/run_deployment.py restart actions
python scripts/run_deployment.py restart web
python scripts/run_deployment.py restart bot
python scripts/run_deployment.py restart jupyter
```

`bot` restarts `web` in the Docker `web` mode because the Teams bot endpoint is
served by FastAPI at `/api/messages`. Restarting `web` also reloads its UI and
bot Python code. Restart `actions` after editing Rasa actions, and `rasa` after
changing or replacing a trained model. Use `all` when several services changed.
Refresh your browser after a UI update.

The command sends a request through a local Unix socket to the existing
supervisor. It stops and starts only the selected child process; PID 1 and the
container stay running. The socket has owner-only permissions. If the requested
service is not part of the running deployment mode, the command reports an
error without stopping other services.

The supervisor itself is loaded only when the container starts. Installing this
restart feature requires **one normal container/App Service restart**. Later
changes to `scripts/run_deployment.py` or its startup configuration also need a
container restart, while changes to its child services can use the commands
above. Rebuilding an image does not change files inside an already running
container; deploy the image first.

Avoid `kill -9 1` and starting a second `run_deployment.py web` process. Those
commands cannot reload the current supervisor safely.
