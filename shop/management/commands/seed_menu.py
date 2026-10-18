import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import Category, MenuItem, StoreSettings


class Command(BaseCommand):
    help = 'Import the original menu without duplicating records or overwriting edits.'

    @transaction.atomic
    def handle(self, *args, **kwargs):
        source = Path(__file__).resolve().parents[2] / 'menu_seed.json'
        added = 0
        for position, group in enumerate(json.loads(source.read_text(encoding='utf-8'))):
            category, _ = Category.objects.get_or_create(key=group['key'], defaults={'name': group['name'], 'position': position, 'is_addon': group['key'] == 'milkAlt'})
            for index, item in enumerate(group['items']):
                _, created = MenuItem.objects.get_or_create(source_key=f'{group["key"]}-{index}', defaults={**item, 'category': category, 'allows_milk': group['key'] in ['coffee', 'frappe']})
                added += created
        StoreSettings.objects.get_or_create(pk=1)
        self.stdout.write(self.style.SUCCESS(f'Imported {added} new entries. Existing edits preserved.'))
