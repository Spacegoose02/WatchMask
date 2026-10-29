import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0016_cinemask'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BingeBanditAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('base_url', models.URLField(help_text='BingeBandit server URL')),
                ('api_key', models.TextField(help_text='Encrypted BingeBandit API key')),
                ('username', models.CharField(blank=True, default='', help_text='BingeBandit username the API key belongs to', max_length=255)),
                ('webhook_usernames', models.TextField(blank=True, default='', help_text='Comma-separated BingeBandit usernames whose webhook events are tracked')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='bingebandit_account', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'BingeBandit account',
                'verbose_name_plural': 'BingeBandit accounts',
            },
        ),
    ]
