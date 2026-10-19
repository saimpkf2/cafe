import json
import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from .models import MenuItem, Category, StoreSettings, Order, OrderEvent


class OrderingTests(TestCase):
    def test_options_are_validated_and_saved_as_order_snapshots(self):
        item = MenuItem.objects.get(name='Espresso')
        item.options = ['Hot', 'Cold']
        item.options_required = True
        item.save()
        payload = self.payload()
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        payload['items'][0]['option'] = 'Invalid'
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        payload['items'][0]['option'] = 'Cold'
        response = self.post('checkout', payload)
        self.assertEqual(response.status_code, 201, response.content)
        order = Order.objects.get(public_id=response.json()['token'])
        self.assertEqual(order.items.get().option_name, 'Cold')
        item.options = ['Hot']
        item.save()
        self.assertEqual(order.items.get().option_name, 'Cold')
        payload['request_key'] = str(uuid.uuid4())
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        self.client.force_login(self.admin)
        data = {'id': item.id, 'category': item.category_id, 'name': item.name, 'description': '', 'price': str(item.price), 'options': ['Hot', 'hot']}
        self.assertEqual(self.post('menu-item', data).status_code, 400)
        data['options'] = [' Hot ', 'Cold']
        self.assertEqual(self.post('menu-item', data).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.options, [{'name': 'Hot', 'price': None}, {'name': 'Cold', 'price': None}])

    def test_option_prices_are_optional_validated_and_snapshotted(self):
        item = MenuItem.objects.get(name='Espresso')
        self.client.force_login(self.admin)
        data = {'id': item.id, 'category': item.category_id, 'name': item.name, 'price': str(item.price)}
        for price in ['-1', 'NaN', '1.001', '1000000']:
            self.assertEqual(self.post('menu-item', {**data, 'options': [{'name': 'Cold', 'price': price}]}).status_code, 400)
        data['options'] = [{'name': 'Hot', 'price': ''}, {'name': 'Cold', 'price': '20.50'}, {'name': 'Free', 'price': '0'}]
        self.assertEqual(self.post('menu-item', data).status_code, 200)
        self.client.logout()
        for option, price in [('Hot', '14.75'), ('Cold', '20.50'), ('Free', '0.00')]:
            payload = self.payload()
            payload['items'][0].update(option=option, expected_price=price)
            response = self.post('checkout', payload)
            self.assertEqual(response.status_code, 201, response.content)
            order = Order.objects.get(public_id=response.json()['token'])
            self.assertEqual(order.items.get().unit_price, Decimal(price))
        item.refresh_from_db()
        item.options = [{'name': 'Cold', 'price': '25.00'}]
        item.save()
        payload = self.payload()
        payload['items'][0].update(option='Cold', expected_price='20.50')
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        self.assertEqual(order.items.get().unit_price, Decimal('0.00'))

    def test_optional_and_required_option_configuration(self):
        item = MenuItem.objects.get(name='Espresso')
        self.client.force_login(self.admin)
        data = {'id': item.id, 'category': item.category_id, 'name': item.name, 'price': str(item.price), 'options': [{'name': 'Cold', 'price': '20.50'}], 'options_required': False}
        self.assertEqual(self.post('menu-item', data).status_code, 200)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 201)
        self.assertEqual(self.post('menu-item', {**data, 'options_required': True}).status_code, 200)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        payload = self.payload()
        payload['items'][0].update(option='Cold', expected_price='20.50')
        self.assertEqual(self.post('checkout', payload).status_code, 201)
        self.assertEqual(self.post('menu-item', {**data, 'options_required': True, 'options': []}).status_code, 400)
        self.assertEqual(self.post('menu-item', {**data, 'options_required': False, 'options': []}).status_code, 200)

    def test_product_image_upload_preview_preserve_and_remove(self):
        import base64
        import io
        from PIL import Image
        item = MenuItem.objects.get(name='Espresso')
        data = {'id': item.id, 'category': item.category_id, 'name': item.name, 'price': str(item.price)}
        output = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(output, format='PNG')
        image = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        self.assertEqual(self.post('menu-item', {**data, 'image_data': image}).status_code, 401)
        self.client.force_login(self.admin)
        self.assertEqual(self.post('menu-item', {**data, 'image_data': image}).status_code, 200)
        self.assertEqual(self.post('menu-item', data).status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.image_data)
        menu = self.client.get('/api/menu/').json()
        product = next(i for group in menu['categories'] for i in group['items'] if i['id'] == item.id)
        self.assertNotIn('image_data', product)
        self.client.logout()
        response = self.client.get(product['image'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.client.force_login(self.admin)
        for invalid in ['data:image/svg+xml;base64,PHN2Zy8+', 'data:image/png;base64,invalid', None]:
            self.assertEqual(self.post('menu-item', {**data, 'image_data': invalid}).status_code, 400)
        self.assertEqual(self.post('menu-item', {**data, 'image_data': ''}).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image_data, '')
        self.assertEqual(self.client.get(product['image']).status_code, 404)

    def test_password_change_validation_and_session_rotation(self):
        payload = {'current_password': 'test-password', 'new_password': 'Updated-password-2026', 'confirm_password': 'Updated-password-2026'}
        self.assertEqual(self.post('change-password', payload).status_code, 401)
        self.client.force_login(self.customer)
        self.assertEqual(self.post('change-password', payload).status_code, 401)
        self.client.force_login(self.admin)
        other = Client()
        other.force_login(self.admin)
        self.assertEqual(self.post('change-password', {**payload, 'current_password': 'incorrect'}).status_code, 400)
        self.assertEqual(self.post('change-password', {**payload, 'confirm_password': 'different'}).status_code, 400)
        self.assertEqual(self.post('change-password', {**payload, 'new_password': 'short', 'confirm_password': 'short'}).status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password('test-password'))
        response = self.post('change-password', payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(payload['new_password']))
        self.assertFalse(self.admin.check_password('test-password'))
        self.assertTrue(self.client.get('/api/session/').json()['authenticated'])
        self.assertFalse(other.get('/api/session/').json()['authenticated'])
        fresh = Client()
        self.assertFalse(fresh.login(username='testadmin', password='test-password'))
        self.assertTrue(fresh.login(username='testadmin', password=payload['new_password']))

    def test_password_change_requires_csrf_and_limits_attempts(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.admin)
        self.assertEqual(protected.post('/api/change-password/', '{}', content_type='application/json').status_code, 403)
        self.client.force_login(self.admin)
        for _ in range(10):
            self.assertEqual(self.post('change-password', {}).status_code, 400)
        self.assertEqual(self.post('change-password', {}).status_code, 429)

    def test_order_date_filters_use_uae_day_boundaries(self):
        from datetime import datetime, timezone as tz
        order = self.order()
        self.client.force_login(self.admin)
        # 20:30 UTC is already the next calendar day in the UAE.
        Order.objects.filter(pk=order.pk).update(created_at=datetime(2026, 10, 16, 20, 30, tzinfo=tz.utc))
        self.assertEqual(self.client.get('/api/orders/?start=2026-10-17&end=2026-10-17').json()['count'], 1)
        self.assertEqual(self.client.get('/api/orders/?start=2026-10-16&end=2026-10-16').json()['count'], 0)
        self.assertEqual(self.client.get('/api/orders/?end=2026-10-16').json()['count'], 0)
        self.assertEqual(self.client.get('/api/orders/?start=2026-10-17').json()['count'], 1)
        self.assertEqual(self.client.get('/api/orders/?start=2026-10-18&end=2026-10-17').status_code, 400)
        self.assertEqual(self.client.get('/api/orders/?start=invalid').status_code, 400)

    def test_category_icon_is_saved_and_validated(self):
        self.client.force_login(self.admin)
        category = Category.objects.get(key='coffee')
        self.assertEqual(self.post('category', {'id': category.id, 'name': category.name, 'icon': 'pizza'}).status_code, 200)
        category.refresh_from_db()
        self.assertEqual(category.icon, 'pizza')
        public = self.client.get('/api/menu/').json()['categories']
        self.assertEqual(next(c for c in public if c['id'] == category.id)['icon'], 'pizza')
        self.assertEqual(self.post('category', {'id': category.id, 'name': category.name, 'icon': 'invalid'}).status_code, 400)

    def test_delete_item_preserves_order_and_seed_does_not_restore_it(self):
        order = self.order()
        item = MenuItem.objects.get(name='Espresso')
        self.assertEqual(self.post('menu-delete', {'kind': 'item', 'id': item.id}).status_code, 401)
        self.client.force_login(self.admin)
        self.assertEqual(self.post('menu-delete', {'kind': 'item', 'id': item.id}).status_code, 200)
        menu = self.client.get('/api/menu/').json()['categories']
        self.assertFalse(any(i['id'] == item.id for c in menu for i in c['items']))
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        self.assertEqual(order.items.get().item_name, 'Espresso')
        call_command('seed_menu', verbosity=0)
        item.refresh_from_db()
        self.assertTrue(item.deleted)

    def test_delete_category_removes_items_and_rejects_stale_updates(self):
        item = MenuItem.objects.get(name='Espresso')
        category = item.category
        self.client.force_login(self.admin)
        self.assertEqual(self.post('menu-delete', {'kind': 'category', 'id': category.id}).status_code, 200)
        self.assertFalse(MenuItem.objects.filter(category=category, deleted=False).exists())
        self.assertFalse(any(c['id'] == category.id for c in self.client.get('/api/menu/').json()['categories']))
        self.assertEqual(self.post('category', {'id': category.id, 'name': 'Restore'}).status_code, 404)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)

    @classmethod
    def setUpTestData(cls):
        call_command('seed_menu', verbosity=0)
        cls.admin = get_user_model().objects.create_user('testadmin', password='test-password', is_staff=True)
        cls.customer = get_user_model().objects.create_user('customer', password='test-password')

    def payload(self, **changes):
        item = MenuItem.objects.get(name='Espresso')
        return {'request_key': str(uuid.uuid4()), 'name': 'Guest Test', 'phone': '+971501234567', 'address': 'Apartment 12, Test Street, Dubai', 'area': '', 'notes': '', 'expected_delivery_fee': '0.00', 'items': [{'item_id': item.id, 'quantity': 2, 'addon_id': None, 'expected_price': '14.75'}], **changes}

    def post(self, action, data):
        return self.client.post(f'/api/{action}/', json.dumps(data), content_type='application/json')

    def order(self):
        response = self.post('checkout', self.payload())
        self.assertEqual(response.status_code, 201, response.content)
        return Order.objects.get(public_id=response.json()['token'])

    def test_original_menu_import_is_repeatable_and_preserves_edits(self):
        self.assertEqual(MenuItem.objects.count(), 48)
        self.assertEqual(Category.objects.count(), 8)
        item = MenuItem.objects.get(name='Espresso')
        item.price = Decimal('19.50'); item.save()
        call_command('seed_menu', verbosity=0)
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal('19.50'))
        self.assertEqual(MenuItem.objects.count(), 48)

    def test_guest_checkout_and_secret_receipt(self):
        order = self.order()
        self.assertEqual(order.total, Decimal('29.50'))
        self.assertEqual(order.payment_status, 'pending')
        self.assertEqual(self.client.get(f'/api/receipt/?token={order.public_id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/receipt/?token={order.id}').status_code, 400)
        self.assertEqual(self.client.get(f'/api/receipt/?token={uuid.uuid4()}').status_code, 404)

    def test_duplicate_request_only_creates_one_order(self):
        payload = self.payload()
        first = self.post('checkout', payload)
        second = self.post('checkout', payload)
        self.assertEqual(first.json()['token'], second.json()['token'])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.post('checkout', {**payload, 'name': 'Different'}).status_code, 400)

    def test_prices_are_verified_and_snapshot_is_preserved(self):
        payload = self.payload()
        payload['items'][0]['expected_price'] = '0.01'
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        order = self.order()
        MenuItem.objects.filter(name='Espresso').update(price='25.00', name='Renamed espresso')
        line = order.items.get()
        self.assertEqual(line.unit_price, Decimal('14.75'))
        self.assertEqual(line.item_name, 'Espresso')

    def test_milk_alternatives_can_be_ordered_as_items(self):
        for name in ['Almond Milk', 'Oat Milk', 'Coconut Milk']:
            with self.subTest(name=name):
                item = MenuItem.objects.get(name=name)
                payload = self.payload()
                payload['items'][0].update(item_id=item.id, expected_price=str(item.price))
                response = self.post('checkout', payload)
                self.assertEqual(response.status_code, 201, response.content)
                order = Order.objects.get(public_id=response.json()['token'])
                self.assertEqual(order.items.get().item_name, name)
                self.assertEqual(order.subtotal, item.price * 2)
                item.available = False
                item.save()
                payload['request_key'] = str(uuid.uuid4())
                self.assertEqual(self.post('checkout', payload).status_code, 400)

    def test_milk_addon_and_invalid_addon_combinations(self):
        addon = MenuItem.objects.get(name='Oat Milk')
        payload = self.payload()
        payload['items'][0].update(addon_id=addon.id, expected_price='18.75')
        self.assertEqual(self.post('checkout', payload).status_code, 201)
        self.assertEqual(Order.objects.get().total, Decimal('37.50'))
        payload = self.payload()
        payload['items'][0]['item_id'] = addon.id
        self.assertEqual(self.post('checkout', payload).status_code, 400)
        payload = self.payload()
        payload['items'][0].update(item_id=MenuItem.objects.get(name='Pink Dragon').id, addon_id=addon.id, expected_price='29.20')
        self.assertEqual(self.post('checkout', payload).status_code, 400)

    def test_closed_store_unavailable_item_and_minimum(self):
        StoreSettings.objects.filter(pk=1).update(is_open=False)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        StoreSettings.objects.filter(pk=1).update(is_open=True, minimum_order=50)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        StoreSettings.objects.filter(pk=1).update(minimum_order=0)
        MenuItem.objects.filter(name='Espresso').update(available=False)
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_fee_area_and_invalid_quantities(self):
        StoreSettings.objects.filter(pk=1).update(delivery_fee=5, delivery_areas='Dubai Marina\nJumeirah')
        self.assertEqual(self.post('checkout', self.payload()).status_code, 400)
        self.assertEqual(self.post('checkout', self.payload(area='Dubai Marina')).status_code, 400)
        payload = self.payload(area='Dubai Marina', expected_delivery_fee='5.00')
        self.assertEqual(self.post('checkout', payload).status_code, 201)
        self.assertEqual(Order.objects.get().total, Decimal('34.50'))
        for quantity in [-1, 0, 51, 1.5, True]:
            payload = self.payload(area='Dubai Marina', expected_delivery_fee='5.00')
            payload['items'][0]['quantity'] = quantity
            self.assertEqual(self.post('checkout', payload).status_code, 400)

    def test_private_endpoints_require_staff(self):
        for endpoint in ['orders', 'alerts', 'overview', 'settings', 'order']:
            self.assertEqual(self.client.get(f'/api/{endpoint}/').status_code, 401)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get('/api/orders/').status_code, 401)
        self.assertEqual(self.post('menu-item', {}).status_code, 401)

    def test_seen_is_persistent_and_does_not_accept_order(self):
        order = self.order()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/api/alerts/').json()['count'], 1)
        self.assertEqual(self.post('seen', {'ids': [order.id]}).status_code, 200)
        self.assertEqual(self.client.get('/api/alerts/').json()['count'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')
        self.assertIsNotNone(order.seen_at)

    def test_status_lifecycle_and_collected_sales(self):
        order = self.order()
        self.client.force_login(self.admin)
        self.assertEqual(self.post('order', {'id': order.id, 'status': 'delivered'}).status_code, 400)
        self.assertEqual(self.post('order', {'id': order.id, 'payment_status': 'collected'}).status_code, 400)
        self.assertEqual(Decimal(self.client.get('/api/overview/').json()['sales']), 0)
        for status in ['accepted', 'preparing', 'out_for_delivery', 'delivered']:
            self.assertEqual(self.post('order', {'id': order.id, 'status': status}).status_code, 200)
        self.assertEqual(self.post('order', {'id': order.id, 'payment_status': 'collected'}).status_code, 200)
        stats = self.client.get('/api/overview/').json()
        self.assertEqual(Decimal(stats['sales']), Decimal('29.50'))
        self.assertEqual(Decimal(stats['pending_cod']), 0)
        self.assertEqual(stats['delivered'], 1)
        self.assertEqual(OrderEvent.objects.filter(order=order).count(), 6)
        self.assertEqual(self.post('order', {'id': order.id, 'status': 'cancelled'}).status_code, 400)

    def test_cancelled_orders_excluded_from_order_value(self):
        order = self.order()
        self.client.force_login(self.admin)
        self.post('order', {'id': order.id, 'status': 'cancelled'})
        stats = self.client.get('/api/overview/').json()
        self.assertEqual(stats['cancelled'], 1)
        self.assertEqual(Decimal(stats['order_value']), 0)
        self.assertEqual(Decimal(stats['pending_cod']), 0)

    def test_login_csrf_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/login/', '{}', content_type='application/json').status_code, 403)
        csrf = client.get('/api/session/').json()['csrf']
        response = client.post('/api/login/', json.dumps({'username': 'testadmin', 'password': 'test-password'}), content_type='application/json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/api/orders/').status_code, 200)
        client.post('/api/logout/', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=response.json()['csrf'])
        self.assertEqual(client.get('/api/orders/').status_code, 401)

    def test_invalid_settings_and_menu_data(self):
        self.client.force_login(self.admin)
        for price in ['NaN', '-1', '1.001', 'Infinity']:
            self.assertEqual(self.post('settings', {'is_open': True, 'delivery_fee': price, 'minimum_order': 0}).status_code, 400)
        self.assertEqual(self.post('settings', {'is_open': 'false', 'delivery_fee': 0, 'minimum_order': 0}).status_code, 400)
        item = MenuItem.objects.get(name='Espresso')
        response = self.post('menu-item', {'id': item.id, 'category': item.category_id, 'name': 'Edited', 'price': '16.50', 'description': 'Updated', 'available': False, 'popular': True, 'allows_milk': True})
        self.assertEqual(response.status_code, 200)
        public = self.client.get('/api/menu/').json()['categories'][0]['items'][0]
        self.assertEqual(public['price'], '16.50')
        self.assertFalse(public['available'])

    def test_login_rate_limit(self):
        for _ in range(15):
            self.assertEqual(self.post('login', {'username': 'nobody', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.post('login', {'username': 'nobody', 'password': 'wrong'}).status_code, 429)

    @override_settings(CSRF_TRUSTED_ORIGINS=['http://localhost:5173', 'http://127.0.0.1:5173'])
    def test_vite_origin_login_and_rejection_of_untrusted_origins(self):
        for origin in ['http://localhost:5173', 'http://127.0.0.1:5173']:
            client = Client(enforce_csrf_checks=True)
            token = client.get('/api/session/').json()['csrf']
            response = client.post('/api/login/', json.dumps({'username': 'testadmin', 'password': 'test-password'}), content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=origin)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(client.get('/api/orders/').status_code, 200)
        client = Client(enforce_csrf_checks=True)
        token = client.get('/api/session/').json()['csrf']
        response = client.post('/api/login/', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://untrusted.example')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'csrf_failed')
        missing = client.post('/api/login/', '{}', content_type='application/json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(missing.status_code, 403)
