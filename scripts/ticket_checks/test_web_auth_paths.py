import asyncio
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from teams_app.web_package import auth, main


class WebAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid='signing-key', use='sig', alg='RS256')

    def setUp(self):
        self.env = patch.dict(os.environ, {'ENTRA_CLIENT_ID': 'our-app', 'ENTRA_TENANT_ID': 'our-tenant', 'ENTRA_API_RESOURCE': 'api://example.com/our-app'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def token(self, **overrides):
        claims = {'aud': 'our-app', 'tid': 'our-tenant', 'oid': 'employee',
                  'iss': 'https://login.microsoftonline.com/our-tenant/v2.0', 'exp': int(time.time())+600}
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm='RS256', headers={'kid': 'signing-key'})

    async def verify(self, token):
        with patch.object(auth, '_keys', AsyncMock(return_value=[self.jwk])), \
                patch.object(auth, 'save_verified_identity', side_effect=lambda identity, source: identity):
            return await auth.verify_microsoft_token(token)

    async def test_browser_id_token_and_teams_api_audiences(self):
        for audience in ['our-app', 'api://example.com/our-app']:
            identity = await self.verify(self.token(aud=audience))
            self.assertEqual(identity['aad_object_id'], 'employee')

    async def test_reject_wrong_audience_even_with_correct_azp(self):
        with self.assertRaises(HTTPException) as raised:
            await self.verify(self.token(aud='https://graph.microsoft.com', azp='our-app'))
        self.assertEqual(raised.exception.status_code, 401)

    async def test_tenant_issuer_expiry_and_signature_checked(self):
        for claims in [{'tid': 'other-tenant'}, {'iss': 'https://attacker.example'}, {'exp': int(time.time())-60}]:
            with self.assertRaises(HTTPException):
                await self.verify(self.token(**claims))
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode({'aud': 'our-app', 'tid': 'our-tenant', 'exp': int(time.time())+600}, other_key, algorithm='RS256', headers={'kid': 'signing-key'})
        with self.assertRaises(HTTPException):
            await self.verify(token)

    async def test_signing_key_rotation_reloads_keys(self):
        with patch.object(auth, '_keys', AsyncMock(side_effect=[[], [self.jwk]])) as keys, \
                patch.object(auth, 'save_verified_identity', side_effect=lambda identity, source: identity):
            identity = await auth.verify_microsoft_token(self.token())
        self.assertEqual(identity['aad_object_id'], 'employee')
        self.assertEqual(keys.await_count, 2)


class WebPrefixTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Match Uvicorn/Rasa's runtime loop, including standalone test runs.
        import uvloop
        cls.previous_policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop_policy(cls.previous_policy)

    async def test_pages_assets_and_auth_config_under_both_proxy_modes(self):
        for prefix in ['', '/employeeassist']:
            with patch.object(main, 'BASE_PATH', prefix), patch.object(main.app, 'root_path', prefix), \
                    patch.dict(os.environ, {'ENTRA_REDIRECT_URI': 'https://example.com/employeeassist', 'PUBLIC_BASE_URL': 'https://example.com'+prefix}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='https://example.com') as client:
                    # A proxy may retain or strip the external prefix.
                    for external in ([''] if not prefix else ['', prefix]):
                        for page in ['/', '/privacy-policy', '/terms-n-conditions']:
                            response = await asyncio.wait_for(client.get(external+page), 10)
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertIn(f'<base href="{prefix}/">', response.text)
                            self.assertNotIn('href="/static/', response.text)
                            self.assertIn("base-uri 'self'", response.headers['content-security-policy'])
                        script = await asyncio.wait_for(client.get(external+'/static/app.js'), 10)
                        self.assertEqual(script.status_code, 200)
                        self.assertIn("fetch('auth/config')", script.text)
                        config = await asyncio.wait_for(client.get(external+'/auth/config'), 10)
                        self.assertEqual(config.json()['redirect_uri'], 'https://example.com/employeeassist')
                    source = next(iter(json.loads(main.index_path().read_text())['documents']))
                    payload = {'messages': [{'custom': {'type': 'policy_evidence', 'chunk': {'source': source, 'page': 2}}}]}
                    enriched = await main._enrich_policy_chunk(payload, 'policy question')
                    pdf_url = enriched['chunk']['pdf_url']
                    self.assertEqual(pdf_url, prefix+'/policies/pdfs/'+quote(source, safe='')+'#page=2')
                    pdf = await asyncio.wait_for(client.get(pdf_url), 10)
                    self.assertEqual(pdf.status_code, 200)
                    self.assertTrue(pdf.content.startswith(b'%PDF'))
