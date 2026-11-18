import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import reply_server


class AccountHealthEndpointTest(unittest.TestCase):
    def test_offline_enabled_account_returns_503_with_account_details(self):
        manager = MagicMock()
        manager.account_health_details.return_value = {
            'test-account': {'enabled': True, 'state': 'risk_blocked', 'healthy': False,
                             'task_alive': True, 'needs_manual_verification': True},
        }
        with patch('reply_server.cookie_manager.manager', manager), patch(
                'db_manager.db_manager.get_all_cookies', return_value={}):
            response = TestClient(reply_server.app).get('/health')
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body['status'], 'unhealthy')
        self.assertEqual(body['accounts']['test-account']['state'], 'risk_blocked')

    def test_online_account_returns_200(self):
        manager = MagicMock()
        manager.account_health_details.return_value = {
            'test-account': {'enabled': True, 'state': 'connected', 'healthy': True},
        }
        with patch('reply_server.cookie_manager.manager', manager), patch(
                'db_manager.db_manager.get_all_cookies', return_value={}):
            response = TestClient(reply_server.app).get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['accounts']['test-account']['state'], 'connected')
