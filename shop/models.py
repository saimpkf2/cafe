import uuid
from django.db import models


class Category(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=30, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    is_addon = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['position', 'id']


class MenuItem(models.Model):
    source_key = models.CharField(max_length=150, unique=True, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='items')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    popular = models.BooleanField(default=False)
    available = models.BooleanField(default=True)
    allows_milk = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)


class StoreSettings(models.Model):
    is_open = models.BooleanField(default=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    minimum_order = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_areas = models.TextField(blank=True)


class Order(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_key = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=25)
    address = models.TextField()
    area = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=25, default='new')
    payment_status = models.CharField(max_length=15, default='pending')
    seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['created_at']), models.Index(fields=['seen_at'])]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=150)
    addon_name = models.CharField(max_length=150, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='history')
    message = models.CharField(max_length=200)
    actor = models.CharField(max_length=150, default='Customer')
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    key = models.CharField(max_length=100, unique=True)
    hits = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
