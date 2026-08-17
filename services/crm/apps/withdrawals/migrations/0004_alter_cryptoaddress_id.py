from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('withdrawals', '0003_cryptoaddress'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cryptoaddress',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
