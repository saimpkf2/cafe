from unittest.mock import patch
from django.db import OperationalError
from django.test import TestCase, override_settings


class DeploymentTests(TestCase):
    @override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=['healthcheck.railway.app', 'testserver'])
    def test_health_probe_works_without_https_or_login(self):
        response = self.client.get('/api/health/', HTTP_HOST='healthcheck.railway.app')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get('/api/menu/').status_code, 301)

    def test_database_failure_is_unhealthy_without_details(self):
        with patch('shop.health.connection.cursor', side_effect=OperationalError('private connection details')):
            response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
