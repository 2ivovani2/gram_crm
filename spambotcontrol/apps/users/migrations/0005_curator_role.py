from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_user_personal_rate_user_referral_rate_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('admin', 'Admin'), ('curator', 'Curator'), ('worker', 'Worker')],
                db_index=True,
                default='worker',
                max_length=20,
            ),
        ),
    ]
