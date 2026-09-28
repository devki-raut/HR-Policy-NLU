"""Run after training with the action server listening on localhost:5055."""
import asyncio
from pathlib import Path
from rasa.core.agent import Agent
from rasa.utils.endpoints import EndpointConfig

async def main():
    models = list(Path('models').glob('*.tar.gz'))
    if not models:
        raise SystemExit('Run make train first')
    agent = Agent.load(str(max(models, key=lambda p: p.stat().st_mtime)), action_endpoint=EndpointConfig(url='http://localhost:5055/webhook'))
    for i, (question, expected) in enumerate([
        ('How many days of annual leave do I get?', '20 days'),
        ('How many days can I work remotely?', 'two days'),
        ('When does medical coverage start?', 'first day'),
        ('How do I claim travel expenses?', '30 days'),
        ('How do I report harassment?', 'confidentially'),
        ('How long is the probation period?', '90 days'),
    ]):
        responses = await agent.handle_text(question, sender_id=f'smoke-{i}')
        text = '\n'.join(r.get('text', '') for r in responses)
        assert expected in text and 'Source:' in text, (question, text)
        print(f'PASS: {question}')

if __name__ == '__main__':
    asyncio.run(main())
