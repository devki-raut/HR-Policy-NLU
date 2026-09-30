# EmployeeAssist

## Architecture

User question
-> DIET intent classification
-> `action_answer_policy`
-> filter enabled FAQ entries by predicted intent
-> SentenceTransformer semantic similarity
-> confident match: fixed FAQ answer + source
-> low confidence: PDF RAG fallback

## Source of truth

- `data/policy_intents.json`: reviewed policy intents and NLU examples.
- `data/faq_answers.json`: reviewed FAQ questions, utterances, fixed answers and sources.
- `scripts/sync_intents.py`: build-time generator for Rasa NLU/rules/domain data.

You do **not** need to run `sync_intents.py` for every chat. Run `make sync-intents` after changing either JSON registry, then train again.

## Embeddings

The FAQ matcher loads `sentence-transformers/all-MiniLM-L6-v2` directly inside the Rasa action server. No separate embedding HTTP service and no Rasa `LanguageModelFeaturizer` are required.

## Bot Service

Azure Bot Service ->
APP_ID/CLIENT_ID, CLIENT_SECRET, TENANT_ID

TERMS_URL, PRIVACY_URL -> dummy endpoints
WEBSITE_URL ->

32x32 , 192x192 logo
manifest
zip & upload on developer portal

## Install

```bash
make install
```

## Build and run

```bash
make sync-intents
make train
```

Start the Rasa action server and HTTP API together:

```bash
make start
```

This exposes the action server on port `5055` and the Rasa API on port `5005`.
Both processes log to the same terminal, and `Ctrl+C` stops both.

For an interactive shell, run `make actions` in one terminal and `make chat`
in another.

## Threshold

Default semantic-match threshold is `0.75`.

You can override it through `.env`:

```env
FAQ_EMBEDDING_THRESHOLD=0.80
```

## PDF RAG

Replace the fallback block in `actions/actions.py` with your existing PDF retrieval function/service. The FAQ matcher only returns reviewed fixed answers from `faq_answers.json`; it does not invent policy content.
## Teams deployment

Run the single public application endpoint, including the Teams tab and bot handler:

```bash
make run_deployment
```

See `teams_app/README.md` for separate local and Docker commands.
