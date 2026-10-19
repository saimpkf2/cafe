from django.db import migrations, models


def preserve_required_options(apps, schema_editor):
    Item = apps.get_model('shop', 'MenuItem')
    for item in Item.objects.exclude(options=[]).iterator():
        item.options_required = True
        item.save(update_fields=['options_required'])


class Migration(migrations.Migration):
    dependencies = [('shop', '0004_menuitem_options_orderline_option_name')]
    operations = [
        migrations.AddField(model_name='menuitem', name='options_required', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='menuitem', name='image_data', field=models.TextField(blank=True, default='')),
        migrations.AddField(model_name='menuitem', name='image_key', field=models.CharField(max_length=64, blank=True, default='')),
        migrations.RunPython(preserve_required_options, migrations.RunPython.noop),
    ]
