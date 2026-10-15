import os
import unittest
from unittest.mock import patch
from deploy.check_deployment import checks


class DeploymentConfigTests(unittest.TestCase):
    def setUp(self):
        env = {
            'MICROSOFT_APP_ID': '9e65e729-0e91-4a00-a03d-e8b9d3e9e841',
            'ENTRA_CLIENT_ID': '9e65e729-0e91-4a00-a03d-e8b9d3e9e841',
            'MICROSOFT_TENANT_ID': '11111111-1111-4111-8111-111111111111',
            'ENTRA_TENANT_ID': '11111111-1111-4111-8111-111111111111',
            'PUBLIC_BASE_URL': 'https://dockerpoc-webapp01.azurewebsites.net/employeeassist/',
            'MICROSOFT_APP_PASSWORD': 'test-placeholder', 'TEAMS_MAIL_OAUTH_CONNECTION': 'test-connection',
            'JUPYTER_TOKEN': 'test-token', 'TEAMS_SUPERADMIN_EMAILS': 'admin@example.com',
            'WEB_PORT': '8608', 'WEBSITES_PORT': '8608',
        }
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_complete_configuration_matches_package(self):
        self.assertTrue(all(checks().values()), checks())

    def test_rejects_disabled_auth_multitenant_selector_and_bad_admin(self):
        os.environ.update(ENTRA_AUTH_REQUIRED='false', ENTRA_TENANT_ID='organizations', TEAMS_SUPERADMIN_EMAILS='not-an-email')
        result = checks()
        self.assertFalse(result['signin_required'])
        self.assertFalse(result['tenant_settings_match'])
        self.assertFalse(result['bootstrap_admin_configured'])

    def test_rejects_port_mismatch_wrong_base_and_redirect(self):
        os.environ.update(WEBSITES_PORT='80', ENTRA_REDIRECT_URI='https://wrong.example/',
                          PUBLIC_BASE_URL='https://dockerpoc-webapp01.azurewebsites.net/employeeassist/jupyter/lab')
        result = checks()
        self.assertFalse(result['azure_target_port_matches'])
        self.assertFalse(result['public_url_matches_teams_tab'])
        self.assertFalse(result['redirect_uri_matches_base'])
        os.environ['WEB_PORT'] = 'bad-port'
        self.assertFalse(checks()['web_port_valid'])
