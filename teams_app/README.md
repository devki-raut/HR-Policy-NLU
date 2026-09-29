# Microsoft Teams application

The deployment has two application entry points. Both use the shared Rasa model,
`actions/` package, and policy data from the repository root.

- `web_package`: Teams tab and FastAPI API on port `8610`
- `bot_package`: Azure Bot Service messaging endpoint on port `3978`
- Rasa REST API: port `5005`
- Rasa action server: port `5055`

## Local setup

```bash
cd /home/ojas/HR-Policy-NLU
make install
make train
make run_deployment
```

The single public application endpoint is `http://localhost:8610`:

- Web tab: `GET /`
- API docs: `GET /docs`
- Health: `GET /health`
- Teams/Azure Bot messaging: `POST /api/messages`
- Policy PDFs: `GET /policy-documents/...`

Rasa and the action server run as private dependencies on ports `5005` and
`5055`. Press `Ctrl+C` to stop the complete deployment.

## Docker

Build and run the single-endpoint deployment:

```bash
make docker_deployment
```

Equivalent commands:

```bash
docker build --target deployment -f Dockerfile -t hr-policy-deployment .

docker run --rm \
  -p 8610:8610 \
  -v "$(pwd)/data/policies:/app/data/policies" \
  --env-file .env \
  hr-policy-deployment
```

Only port `8610` is published. The policy mount preserves the index, PDFs, and
generated Markdown. Set `APP_PUBLIC_URL` to the public HTTPS URL so Adaptive
Card PDF links open outside the container.

For Microsoft Teams, configure the Azure Bot messaging endpoint as:

```text
https://YOUR-PUBLIC-DOMAIN/api/messages
```

Microsoft Teams cannot call a localhost endpoint. Use an HTTPS reverse proxy or
tunnel during development and put that public domain in the Teams manifest.
