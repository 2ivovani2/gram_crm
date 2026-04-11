import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_curator_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(blank=True, max_length=500)),
                ('attracted_count', models.PositiveIntegerField(
                    default=0,
                    help_text='Число привлечённых по этой ссылке (замораживается при деактивации)',
                )),
                ('is_active', models.BooleanField(default=True, db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('deactivated_at', models.DateTimeField(blank=True, null=True)),
                ('note', models.CharField(
                    blank=True,
                    help_text='Причина замены / примечание',
                    max_length=255,
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='work_links',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Рабочая ссылка',
                'verbose_name_plural': 'Рабочие ссылки',
                'ordering': ['-created_at'],
            },
        ),
    ]
