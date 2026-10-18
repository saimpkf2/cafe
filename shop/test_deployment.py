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

    @override_settings(ALLOWED_HOSTS=['desirecafe.up.railway.app'], SECURE_SSL_REDIRECT=True)
    def test_storefront_origins_pass_csrf_with_cookie_but_other_origins_fail(self):
        from django.test import Client
        import json
        for origin in ['https://desirescafe.com', 'https://www.desirescafe.com', 'https://untrusted.example']:
            client = Client(enforce_csrf_checks=True)
            headers = {'HTTP_HOST': 'desirecafe.up.railway.app', 'HTTP_X_FORWARDED_PROTO': 'https'}
            token = client.get('/api/session/', **headers).json()['csrf']
            response = client.post('/api/login/', json.dumps({'username': 'nonexistent', 'password': 'invalid'}), content_type='application/json', HTTP_ORIGIN=origin, HTTP_X_CSRFTOKEN=token, **headers)
            self.assertEqual(response.status_code, 403 if 'untrusted' in origin else 401)
            if 'untrusted' not in origin:
                missing_token = Client(enforce_csrf_checks=True).post('/api/login/', '{}', content_type='application/json', HTTP_ORIGIN=origin, **headers)
                self.assertEqual(missing_token.status_code, 403)
