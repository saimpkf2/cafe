from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('shop', '0005_product_image_options_required')]
    operations = [
        migrations.AddField(model_name='menuitem', name='option_mode', field=models.CharField(max_length=10, default='single')),
        migrations.AddField(model_name='menuitem', name='option_groups', field=models.JSONField(default=list, blank=True)),
        migrations.AlterField(model_name='orderline', name='option_name', field=models.TextField(blank=True)),
        migrations.AddField(model_name='orderline', name='selected_options', field=models.JSONField(default=list, blank=True)),
    ]
