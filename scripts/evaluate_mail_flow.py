"""Exercise trained dialogue rules and real draft actions without mail/network calls."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

async def main():
    from rasa.core.agent import Agent
    from rasa.core.actions.action import RemoteAction
    from rasa.shared.core.events import deserialise_events
    from rasa.shared.core.trackers import EventVerbosity
    from rasa_sdk import Tracker
    from rasa_sdk.executor import CollectingDispatcher
    from actions.tickets import ActionStartTicket, ActionDraftTicket, ValidateTicketForm
    from actions.actions import ActionAnswerIT
    classes=[ActionStartTicket,ActionDraftTicket,ValidateTicketForm,ActionAnswerIT]
    actions={cls().name():cls() for cls in classes}
    model=max((ROOT/'models').glob('*.tar.gz'),key=lambda p:p.stat().st_mtime)
    agent=Agent.load(str(model))
    async def execute(action,output_channel,nlg,tracker,domain):
        target=actions[action.name()]
        dispatcher=CollectingDispatcher();result=target.run(dispatcher,Tracker.from_dict(tracker.current_state(EventVerbosity.ALL)),domain.as_dict())
        if asyncio.iscoroutine(result):result=await result
        for message in dispatcher.messages:
            await output_channel.send_response(tracker.sender_id,message)
        return deserialise_events(result)
    results=[]
    with patch.object(RemoteAction,'run',execute):
        for i,text in enumerate(['Create IT ticket','Create IT ticket for an internet issue','internet issue']):
            sender='mail-flow-evaluation-'+str(i)
            messages=await agent.handle_text(text,sender_id=sender)
            drafts=[m['custom'] for m in messages if (m.get('custom') or {}).get('type')=='ticket_draft']
            if i<2:
                assert len(drafts)==1,(text,messages)
                assert drafts[0].get('cc')=='',drafts
                if i==0:assert drafts[0]['body']==''
                else:assert 'internet issue' in drafts[0]['body']
            else:
                assert not drafts,(text,messages)
                assert any(m.get('text') for m in messages)
            tracker=await agent.tracker_store.retrieve(sender)
            assert not tracker.active_loop,tracker.active_loop
            results.append({'question':text,'draft_created':bool(drafts),'active_loop':tracker.active_loop,'messages':messages})
        # Simulate a conversation that was already at the old description step.
        from rasa.shared.core.events import SessionStarted, ActionExecuted, ActiveLoop, SlotSet, UserUttered
        sender='mail-flow-evaluation-legacy'
        tracker=await agent.tracker_store.get_or_create_tracker(sender)
        for event in [SessionStarted(),ActionExecuted('action_listen'),UserUttered('Create IT ticket',intent={'name':'contact_support','confidence':1.0}),ActionExecuted('ticket_form'),ActiveLoop('ticket_form'),SlotSet('requested_slot','ticket_details'),ActionExecuted('action_listen')]:
            tracker.update(event)
        await agent.tracker_store.save(tracker)
        messages=await agent.handle_text('internet issue',sender_id=sender)
        drafts=[m['custom'] for m in messages if (m.get('custom') or {}).get('type')=='ticket_draft']
        assert len(drafts)==1,messages
        assert 'internet issue' in drafts[0]['body'],drafts
        tracker=await agent.tracker_store.retrieve(sender)
        assert not tracker.active_loop,tracker.active_loop
        results.append({'question':'internet issue (legacy description step)','draft_created':True,'active_loop':tracker.active_loop,'messages':messages})
    output={'model':model.name,'checks_passed':len(results),'results':results}
    (ROOT/'artifacts/logs/mail_dialogue_evaluation.json').write_text(json.dumps(output,indent=2)+'\n')
    print('Passed',len(results),'trained dialogue checks using real draft/FAQ actions.')

if __name__=='__main__':asyncio.run(main())
