import base64
import binascii
import io
from PIL import Image, UnidentifiedImageError
import hashlib
import json
import re
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum, Prefetch
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .models import Category, MenuItem, StoreSettings, Order, OrderLine, OrderEvent, RateBucket


class Invalid(Exception):
    pass


def csrf_failure(request, reason=''):
    return JsonResponse({
        'error': 'Your security token could not be verified. Refresh the page and try again.',
        'code': 'csrf_failed',
    }, status=403)


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('999999.99') or result != result.quantize(Decimal('.01')):
            raise Invalid('Enter a valid amount with at most two decimal places.')
        return result
    except (InvalidOperation, ValueError):
        raise Invalid('Enter a valid amount.')


def product_image(value):
    if value == '':
        return ''
    if not isinstance(value, str) or len(value) > 410000:
        raise Invalid('Choose an image up to 300 KB after resizing.')
    try:
        header, encoded = value.split(',', 1)
        if header not in ('data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'):
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 300000:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP') or max(image.size) > 1200:
                raise ValueError()
            image.load()
            output = io.BytesIO()
            image.convert('RGB').save(output, format='JPEG', quality=85)
        return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise Invalid('Choose a valid JPG, PNG or WebP product image.')


def product_options(values):
    if not isinstance(values, list) or len(values) > 30:
        raise Invalid('Add up to 30 product options.')
    result = []
    for value in values:
        value = {'name': value} if isinstance(value, str) else value
        if not isinstance(value, dict) or not isinstance(value.get('name'), str) or not 1 <= len(value['name'].strip()) <= 100:
            raise Invalid('Option names must be between 1 and 100 characters.')
        price = value.get('price')
        result.append({'name': value['name'].strip(), 'price': None if price is None or price == '' else str(money(price).quantize(Decimal('.01')))})
    if len({o['name'].casefold() for o in result}) != len(result):
        raise Invalid('Option names must be unique.')
    return result


def product_option_groups(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise Invalid('Add between 1 and 10 option categories.')
    result = []
    for group in values:
        if not isinstance(group, dict):
            raise Invalid('Invalid option category.')
        name = text(group, 'name', 100)
        options = product_options(group.get('options'))
        if not options:
            raise Invalid('Add at least one option to ' + name + '.')
        result.append({'name': name, 'required': boolean(group, 'required'), 'options': options})
    if len({g['name'].casefold() for g in result}) != len(result):
        raise Invalid('Option category names must be unique.')
    return result


def selected_product_options(item, line):
    single = line.get('option', '')
    selections = line.get('selections', [])
    if not isinstance(single, str) or not isinstance(selections, list):
        raise Invalid('Invalid product choices.')
    if item.option_mode != 'multi':
        choice = next((o for o in product_options(item.options) if o['name'] == single), None)
        if selections or (single and choice is None) or (item.options_required and choice is None):
            raise Invalid('Choose a current option for ' + item.name + '. Please update your cart.')
        return single, [], money(choice['price']) if choice and choice['price'] is not None else item.price
    if single or len(selections) > 10:
        raise Invalid('Please update the choices for ' + item.name + '.')
    groups = product_option_groups(item.option_groups)
    chosen = {}
    for selection in selections:
        if not isinstance(selection, dict) or not isinstance(selection.get('category'), str) or not isinstance(selection.get('option'), str):
            raise Invalid('Invalid product choice.')
        category = selection['category']
        if category in chosen or category not in {g['name'] for g in groups}:
            raise Invalid('Choose only one option per current category.')
        chosen[category] = selection['option']
    price, snapshot = item.price, []
    for group in groups:
        name = group['name']
        choice = next((o for o in group['options'] if o['name'] == chosen.get(name)), None)
        if (name in chosen or group['required']) and choice is None:
            raise Invalid('Choose a current option for ' + name + ' in ' + item.name + '.')
        if choice:
            extra = money(choice['price']) if choice['price'] is not None else Decimal('0')
            price += extra
            snapshot.append({'category': name, 'option': choice['name'], 'price': str(extra)})
    return ' · '.join(s['category'] + ': ' + s['option'] for s in snapshot), snapshot, money(price)


def text(data, key, limit, required=True):
    value = str(data.get(key, '')).strip()
    if (required and not value) or len(value) > limit:
        raise Invalid(f'{key.replace("_", " ").title()} is required and must be within {limit} characters.')
    return value


def boolean(data, key, default=False):
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise Invalid(f'{key} must be true or false.')
    return value


def rate_limit(request, label, limit, seconds):
    now = timezone.now()
    # Trust only REMOTE_ADDR. Configure your reverse proxy accordingly.
    address = request.META.get('REMOTE_ADDR', '')
    key = hashlib.sha256(f'{label}:{address}'.encode()).hexdigest()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={'expires_at': now + timedelta(seconds=seconds)})
        bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
        if bucket.expires_at <= now:
            bucket.hits = 0
            bucket.expires_at = now + timedelta(seconds=seconds)
        if bucket.hits >= limit:
            return False
        bucket.hits += 1
        bucket.save()
    return True


def config():
    return StoreSettings.objects.get_or_create(pk=1)[0]


def settings_json(s):
    return {'is_open': s.is_open, 'delivery_fee': str(s.delivery_fee), 'minimum_order': str(s.minimum_order), 'delivery_areas': s.delivery_areas, 'currency': 'AED', 'payment_method': 'cod'}


def menu_json():
    return [{'id': c.id, 'key': c.key, 'name': c.name, 'icon': c.icon, 'position': c.position, 'is_addon': c.is_addon, 'items': [{'id': i.id, 'category': c.id, 'name': i.name, 'description': i.description, 'price': str(i.price), 'popular': i.popular, 'available': i.available, 'allows_milk': i.allows_milk, 'options': product_options(i.options), 'options_required': i.options_required, 'option_mode': i.option_mode, 'option_groups': i.option_groups, 'image': f'/api/menu-image/?id={i.id}&v={i.image_key}' if i.image_key else ''} for i in c.menu_items]} for c in Category.objects.filter(deleted=False).prefetch_related(Prefetch('items', queryset=MenuItem.objects.filter(deleted=False).defer('image_data').order_by('id'), to_attr='menu_items'))]


def order_json(o, detail=False):
    data = {'id': o.id, 'order_number': f'DC-{o.id:06}', 'name': o.name, 'phone': o.phone, 'total': str(o.total), 'status': o.status, 'payment_status': o.payment_status, 'seen': o.seen_at is not None, 'created_at': o.created_at.isoformat()}
    if detail:
        data.update(address=o.address, area=o.area, notes=o.notes, subtotal=str(o.subtotal), delivery_fee=str(o.delivery_fee), items=[{'name': x.item_name, 'addon': x.addon_name, 'option': x.option_name, 'selections': x.selected_options, 'unit_price': str(x.unit_price), 'quantity': x.quantity} for x in o.items.all()], history=[{'message': e.message, 'actor': e.actor, 'created_at': e.created_at.isoformat()} for e in o.history.order_by('id')])
    return data


def create_order(request, data):
    try:
        request_key = uuid.UUID(str(data.get('request_key', '')))
    except ValueError:
        raise Invalid('A valid checkout request key is required.')
    digest = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != 'request_key'}, sort_keys=True).encode()).hexdigest()
    existing = Order.objects.filter(request_key=request_key).first()
    if existing:
        if existing.request_hash != digest:
            raise Invalid('This checkout has already been submitted with different details.')
        return JsonResponse({'token': str(existing.public_id), 'order_number': f'DC-{existing.id:06}'})
    if not rate_limit(request, 'checkout', 20, 3600):
        return JsonResponse({'error': 'Too many orders. Please try again later.'}, status=429)
    name = text(data, 'name', 100)
    phone = text(data, 'phone', 25)
    if not re.fullmatch(r'\+?[\d ()-]{7,25}', phone) or not 7 <= len(re.sub(r'\D', '', phone)) <= 15:
        raise Invalid('Enter a valid phone number including country code.')
    address = text(data, 'address', 1000)
    if len(address) < 10:
        raise Invalid('Please enter a complete delivery address (at least 10 characters).')
    area = text(data, 'area', 100, False)
    notes = text(data, 'notes', 1000, False)
    lines = data.get('items')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise Invalid('Your cart must contain between 1 and 50 lines.')
    with transaction.atomic():
        s = config()
        if not s.is_open:
            raise Invalid('Ordering is currently closed.')
        areas = [x.strip() for x in s.delivery_areas.splitlines() if x.strip()]
        if areas and area not in areas:
            raise Invalid('Please choose one of our delivery areas.')
        result, subtotal = [], Decimal('0')
        for line in lines:
            if not isinstance(line, dict):
                raise Invalid('Invalid cart item.')
            quantity = line.get('quantity')
            if type(quantity) is not int or not 1 <= quantity <= 50:
                raise Invalid('Quantity must be between 1 and 50.')
            item = MenuItem.objects.select_related('category').filter(pk=line.get('item_id'), available=True, deleted=False, category__deleted=False).first()
            if not item:
                raise Invalid('An item in your cart is unavailable. Please update your cart.')
            selected_option, selected_options, price = selected_product_options(item, line)
            addon = None
            if line.get('addon_id'):
                addon = MenuItem.objects.select_related('category').filter(pk=line['addon_id'], available=True, deleted=False, category__deleted=False, category__is_addon=True).first()
                if not addon or not item.allows_milk:
                    raise Invalid('This milk option is unavailable for the selected item.')
                price += addon.price
            if money(line.get('expected_price')) != price:
                raise Invalid('Menu prices have changed. Review your refreshed cart and submit again.')
            result.append(OrderLine(item_name=item.name, option_name=selected_option, selected_options=selected_options, addon_name=addon.name if addon else '', unit_price=price, quantity=quantity))
            subtotal += price * quantity
        if subtotal < s.minimum_order:
            raise Invalid(f'Minimum order is AED {s.minimum_order}.')
        if money(data.get('expected_delivery_fee')) != s.delivery_fee:
            raise Invalid('Delivery fee has changed. Review checkout and submit again.')
        if subtotal + s.delivery_fee > Decimal('999999.99'):
            raise Invalid('This order exceeds the supported total. Please contact the café.')
        o = Order.objects.create(request_key=request_key, request_hash=digest, name=name, phone=phone, address=address, area=area, notes=notes, subtotal=subtotal, delivery_fee=s.delivery_fee, total=subtotal + s.delivery_fee)
        for line in result:
            line.order = o
        OrderLine.objects.bulk_create(result)
        OrderEvent.objects.create(order=o, message='Order placed · Cash on Delivery')
    return JsonResponse({'token': str(o.public_id), 'order_number': f'DC-{o.id:06}'}, status=201)


def dashboard(request):
    today = timezone.localdate()
    try:
        start = datetime.strptime(request.GET.get('start', today.isoformat()), '%Y-%m-%d').date()
        end = datetime.strptime(request.GET.get('end', today.isoformat()), '%Y-%m-%d').date()
    except ValueError:
        raise Invalid('Invalid date range.')
    if end < start or (end - start).days > 366:
        raise Invalid('Choose a date range of at most 366 days.')
    orders = Order.objects.filter(created_at__gte=timezone.make_aware(datetime.combine(start, time.min)), created_at__lt=timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))
    total = lambda qs: str(qs.aggregate(value=Sum('total'))['value'] or Decimal('0.00'))
    live = orders.exclude(status='cancelled')
    days = list(orders.filter(payment_status='collected').annotate(day=TruncDate('created_at')).values('day').annotate(sales=Sum('total'), count=Count('id')).order_by('day'))
    popular = list(OrderLine.objects.filter(order__in=live).values('item_name').annotate(quantity=Sum('quantity')).order_by('-quantity')[:5])
    return {'count': orders.count(), 'order_value': total(live), 'sales': total(orders.filter(payment_status='collected')), 'pending_cod': total(live.filter(payment_status='pending')), 'pending': live.exclude(status='delivered').count(), 'delivered': orders.filter(status='delivered').count(), 'cancelled': orders.filter(status='cancelled').count(), 'chart': days, 'popular': popular, 'recent': [order_json(o) for o in orders[:8]]}


@never_cache
def dispatch(request, action):
    try:
        return handle(request, action)
    except (Invalid, ValueError, TypeError, KeyError) as error:
        return JsonResponse({'error': str(error) if isinstance(error, Invalid) else 'Invalid request data.'}, status=400)
    except (Order.DoesNotExist, Category.DoesNotExist, MenuItem.DoesNotExist):
        return JsonResponse({'error': 'Record not found.'}, status=404)
    except IntegrityError:
        return JsonResponse({'error': 'Conflicting update. Refresh and try again.'}, status=409)


def handle(request, action):
    methods = {'session': ['GET'], 'login': ['POST'], 'logout': ['POST'], 'change-password': ['POST'], 'menu': ['GET'], 'menu-image': ['GET'], 'checkout': ['POST'], 'receipt': ['GET'], 'overview': ['GET'], 'orders': ['GET'], 'order': ['GET', 'POST'], 'seen': ['POST'], 'menu-delete': ['POST'], 'menu-item': ['POST'], 'category': ['POST'], 'settings': ['GET', 'POST'], 'alerts': ['GET']}
    if action not in methods:
        return JsonResponse({'error': 'Not found.'}, status=404)
    if request.method not in methods[action]:
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    public = {'session', 'login', 'menu', 'menu-image', 'checkout', 'receipt'}
    if action not in public and not (request.user.is_authenticated and request.user.is_active and request.user.is_staff):
        return JsonResponse({'error': 'Please sign in as an administrator.'}, status=401)
    data = {}
    if request.method == 'POST':
        if len(request.body) > (500000 if action == 'menu-item' else 50000):
            raise Invalid('Request too large.')
        try:
            data = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            raise Invalid('Invalid JSON.')
        if not isinstance(data, dict):
            raise Invalid('Expected an object.')
    if action == 'session':
        return JsonResponse({'csrf': get_token(request), 'authenticated': request.user.is_authenticated and request.user.is_staff, 'username': request.user.get_username()})
    if action == 'login':
        if not rate_limit(request, 'login', 15, 900):
            return JsonResponse({'error': 'Too many login attempts. Try again in 15 minutes.'}, status=429)
        user = authenticate(request, username=data.get('username'), password=data.get('password'))
        if not user or not user.is_staff:
            return JsonResponse({'error': 'Invalid administrator credentials.'}, status=401)
        login(request, user)
        return JsonResponse({'csrf': get_token(request), 'username': user.username})
    if action == 'change-password':
        if not rate_limit(request, f'password-change-{request.user.pk}', 10, 900):
            return JsonResponse({'error': 'Too many attempts. Try again in 15 minutes.'}, status=429)
        current, new, confirm = (data.get(key) for key in ('current_password', 'new_password', 'confirm_password'))
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in (current, new, confirm)):
            raise Invalid('Fill all password fields (maximum 256 characters).')
        if not request.user.check_password(current):
            raise Invalid('Current password is incorrect.')
        if new != confirm:
            raise Invalid('New passwords do not match.')
        if new == current:
            raise Invalid('Choose a different new password.')
        try:
            validate_password(new, request.user)
        except ValidationError as error:
            raise Invalid(' '.join(error.messages))
        request.user.set_password(new)
        request.user.save(update_fields=['password'])
        update_session_auth_hash(request, request.user)
        return JsonResponse({'ok': True, 'csrf': get_token(request)})
    if action == 'logout':
        logout(request)
        return JsonResponse({'ok': True})
    if action == 'menu-image':
        item = MenuItem.objects.get(pk=request.GET.get('id'), deleted=False, category__deleted=False)
        if not item.image_data:
            return HttpResponse(status=404)
        header, encoded = item.image_data.split(',', 1)
        return HttpResponse(base64.b64decode(encoded), content_type=header[5:].split(';')[0])
    if action == 'menu':
        return JsonResponse({'categories': menu_json(), 'settings': settings_json(config()), 'features': ['multi_category_options']})
    if action == 'checkout':
        return create_order(request, data)
    if action == 'receipt':
        o = Order.objects.get(public_id=uuid.UUID(request.GET.get('token', '')))
        return JsonResponse(order_json(o, True))
    if action == 'alerts':
        unseen = Order.objects.filter(seen_at__isnull=True)
        return JsonResponse({'count': unseen.count(), 'orders': [order_json(o) for o in unseen[:30]]})
    if action == 'overview':
        return JsonResponse(dashboard(request))
    if action == 'orders':
        orders = Order.objects.all()
        from django.db.models import Q
        q = request.GET.get('q', '').strip()[:100]
        if q:
            query = Q(name__icontains=q) | Q(phone__icontains=q)
            number = q.upper().removeprefix('DC-')
            if number.isdigit():
                query |= Q(pk=int(number))
            orders = orders.filter(query)
        if request.GET.get('status'):
            orders = orders.filter(status=request.GET['status'])
        start_text, end_text = request.GET.get('start', ''), request.GET.get('end', '')
        try:
            start = datetime.strptime(start_text, '%Y-%m-%d').date() if start_text else None
            end = datetime.strptime(end_text, '%Y-%m-%d').date() if end_text else None
        except ValueError:
            raise Invalid('Choose valid order dates.')
        if start and end and end < start:
            raise Invalid('End date must be on or after start date.')
        if start:
            orders = orders.filter(created_at__gte=timezone.make_aware(datetime.combine(start, time.min)))
        if end:
            orders = orders.filter(created_at__lte=timezone.make_aware(datetime.combine(end, time.max)))
        page = max(1, int(request.GET.get('page', 1)))
        return JsonResponse({'count': orders.count(), 'orders': [order_json(o) for o in orders[(page-1)*25:page*25]]})
    if action == 'seen':
        ids = data.get('ids', [])
        if not isinstance(ids, list) or len(ids) > 100 or any(type(i) is not int for i in ids):
            raise Invalid('Invalid order IDs.')
        Order.objects.filter(pk__in=ids, seen_at__isnull=True).update(seen_at=timezone.now())
        return JsonResponse({'ok': True})
    if action == 'order':
        if request.method == 'GET':
            return JsonResponse(order_json(Order.objects.get(pk=request.GET.get('id')), True))
        transitions = {'new': ['accepted', 'cancelled'], 'accepted': ['preparing', 'cancelled'], 'preparing': ['out_for_delivery', 'cancelled'], 'out_for_delivery': ['delivered', 'cancelled'], 'delivered': [], 'cancelled': []}
        with transaction.atomic():
            o = Order.objects.select_for_update().get(pk=data.get('id'))
            if data.get('status') and data['status'] != o.status:
                if data['status'] not in transitions[o.status] or o.payment_status == 'collected':
                    raise Invalid('This status transition is not allowed. Refresh the order.')
                o.status = data['status']
                OrderEvent.objects.create(order=o, message=f'Status changed to {o.status.replace("_", " ")}', actor=request.user.username)
            if data.get('payment_status') and data['payment_status'] != o.payment_status:
                if data['payment_status'] != 'collected' or o.status != 'delivered':
                    raise Invalid('Cash can only be marked collected for a delivered order.')
                o.payment_status = 'collected'
                OrderEvent.objects.create(order=o, message='COD cash marked collected', actor=request.user.username)
            o.save()
        return JsonResponse(order_json(o, True))
    if action == 'settings':
        s = config()
        if request.method == 'POST':
            s.is_open = boolean(data, 'is_open')
            s.delivery_fee = money(data.get('delivery_fee'))
            s.minimum_order = money(data.get('minimum_order'))
            s.delivery_areas = text(data, 'delivery_areas', 5000, False)
            if any(len(x.strip()) > 100 for x in s.delivery_areas.splitlines()):
                raise Invalid('Each delivery area must be within 100 characters.')
            s.save()
        return JsonResponse(settings_json(s))
    if action == 'menu-delete':
        with transaction.atomic():
            if data.get('kind') == 'category':
                category = Category.objects.select_for_update().get(pk=data.get('id'), deleted=False)
                category.items.update(deleted=True, available=False)
                category.deleted = True
                category.save(update_fields=['deleted'])
            elif data.get('kind') == 'item':
                item = MenuItem.objects.select_for_update().get(pk=data.get('id'), deleted=False)
                item.deleted = True
                item.available = False
                item.save(update_fields=['deleted', 'available'])
            else:
                raise Invalid('Choose an item or category to delete.')
        return JsonResponse({'ok': True})
    if action == 'category':
        c = Category.objects.get(pk=data['id'], deleted=False) if data.get('id') else Category(key=f'custom-{uuid.uuid4().hex[:12]}')
        c.name = text(data, 'name', 100)
        icon = data.get('icon', c.icon)
        allowed_icons = {'coffee', 'cup-soda', 'glass-water', 'milk', 'droplets', 'ice-cream', 'cake', 'cake-slice', 'cookie', 'croissant', 'sandwich', 'pizza', 'burger', 'salad', 'soup', 'beef', 'fish', 'egg', 'egg-fried', 'apple', 'banana', 'cherry', 'citrus', 'grape', 'carrot', 'wheat', 'leaf', 'flame', 'utensils', 'candy'}
        if not isinstance(icon, str) or (icon and icon not in allowed_icons):
            raise Invalid('Choose a valid category icon.')
        c.icon = icon
        c.position = max(0, int(data.get('position', 0)))
        # Existing category types stay stable to preserve add-on relationships.
        if not c.pk:
            c.is_addon = boolean(data, 'is_addon')
        c.save()
        return JsonResponse({'ok': True})
    if action == 'menu-item':
        i = MenuItem.objects.get(pk=data['id'], deleted=False) if data.get('id') else MenuItem()
        i.category = Category.objects.get(pk=data.get('category'), deleted=False)
        i.option_mode = data.get('option_mode', i.option_mode)
        if i.option_mode not in ('single', 'multi'):
            raise Invalid('Choose Single Category Option or Multi Category Option.')
        if i.option_mode == 'multi':
            i.option_groups = product_option_groups(data.get('option_groups', i.option_groups))
        else:
            i.options = product_options(data.get('options', i.options))
            i.options_required = boolean(data, 'options_required', i.options_required)
            if i.options_required and not i.options:
                raise Invalid('Add at least one option or uncheck Option Compulsory.')
        if 'image_data' in data:
            i.image_data = product_image(data['image_data'])
            i.image_key = hashlib.sha256(i.image_data.encode()).hexdigest() if i.image_data else ''
        i.name = text(data, 'name', 150)
        i.description = text(data, 'description', 1000, False)
        i.price = money(data.get('price'))
        i.popular = boolean(data, 'popular')
        i.available = boolean(data, 'available', True)
        i.allows_milk = boolean(data, 'allows_milk') and not i.category.is_addon
        i.save()
        return JsonResponse({'ok': True, 'id': i.id})
