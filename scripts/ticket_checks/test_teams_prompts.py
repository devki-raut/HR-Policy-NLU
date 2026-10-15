import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from teams_app.bot_package.bot_handler import (
    TeamsBot, _prompt_suggestions_card, _welcome_card,
    HR_PROMPT_QUESTIONS, IT_PROMPT_QUESTIONS, REPORT_PROMPT_QUESTIONS,
)

ROOT=Path(__file__).resolve().parents[2]

class TeamsPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_every_prompt_sends_the_question_and_groups_are_complete(self):
        card=_prompt_suggestions_card()
        expected=[HR_PROMPT_QUESTIONS,IT_PROMPT_QUESTIONS,REPORT_PROMPT_QUESTIONS]
        for action,questions in zip(card['actions'][:3],expected):
            submits=[a for row in action['card']['body'][1:] for a in row['actions']]
            self.assertEqual([a['data']['msteams']['text'] for a in submits],[q for _,q in questions])
            self.assertTrue(all(len(row['actions'])<=3 for row in action['card']['body'][1:]))
        self.assertEqual(card['body'][0]['text'],'Prompt Suggestions from EmployeeAssist')
        self.assertEqual(card['actions'][-1]['data']['msteams']['text'],'Create IT ticket')

    def test_welcome_exposes_prompt_list(self):
        card=_welcome_card('Employee')
        self.assertTrue(any(a.get('data',{}).get('msteams',{}).get('text')=='Prompt suggestions'
                            for row in card['body'] for a in row.get('actions',[])))

    async def test_prompt_commands_are_handled_without_calling_rasa(self):
        for text in ['Prompt suggestions','SHOW SUGGESTIONS','show prompt suggestions']:
            context=SimpleNamespace(activity=SimpleNamespace(text=text,value=None),send_activity=AsyncMock())
            with patch('teams_app.bot_package.bot_handler._teams_identity',AsyncMock(return_value={})), patch('teams_app.bot_package.bot_handler.httpx.AsyncClient') as http:
                await TeamsBot().on_message_activity(context)
            http.assert_not_called()
            sent=context.send_activity.await_args.args[0]
            self.assertEqual(sent.attachments[0].content['body'][0]['text'],'Prompt Suggestions from EmployeeAssist')

    def test_manifest_menu_and_upload_archive_match(self):
        for path in [ROOT/'teams_app/app_package/manifest.json',ROOT/'teams_app/app_package/upload/manifest.json']:
            manifest=json.loads(path.read_text())
            commands=manifest['bots'][0]['commandLists'][0]['commands']
            self.assertLessEqual(len(commands),10)
            self.assertTrue(all(len(c['title'])<=32 and len(c['description'])<=128 for c in commands))
            self.assertIn('Prompt suggestions',[c['title'] for c in commands])
            self.assertFalse(any('broadcast' in c['title'].lower() for c in commands))
        folder=ROOT/'teams_app/app_package/upload'
        with zipfile.ZipFile(folder/'EmployeeAssist-1.0.6.zip') as z:
            self.assertEqual(set(z.namelist()),{'manifest.json','color.png','outline.png'})
            self.assertEqual(z.read('manifest.json'),(folder/'manifest.json').read_bytes())
