# HR Policy Rasa FAQ Bot

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

## Install

```bash
make install
```

## Build and run

```bash
make sync-intents
make train
```

Terminal 1:

```bash
make actions
```

Terminal 2:

```bash
make chat
```

## Threshold

Default semantic-match threshold is `0.75`.

You can override it through `.env`:

```env
FAQ_EMBEDDING_THRESHOLD=0.80
```

## PDF RAG

Replace the fallback block in `actions/actions.py` with your existing PDF retrieval function/service. The FAQ matcher only returns reviewed fixed answers from `faq_answers.json`; it does not invent policy content.
