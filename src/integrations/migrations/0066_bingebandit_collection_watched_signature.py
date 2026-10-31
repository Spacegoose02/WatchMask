from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0065_bingebandit_library_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="bingebanditaccount",
            name="webhook_secret",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Encrypted secret BingeBandit signs webhooks with; empty skips the check",
            ),
        ),
        migrations.AddField(
            model_name="bingebanditaccount",
            name="sync_collection",
            field=models.BooleanField(
                default=True,
                help_text="Mark the movies and shows on the server as collected",
            ),
        ),
        migrations.AddField(
            model_name="bingebanditaccount",
            name="push_watched",
            field=models.BooleanField(
                default=False,
                help_text="Mark titles watched on BingeBandit when they are completed here",
            ),
        ),
        migrations.AlterField(
            model_name="collectionsourcestate",
            name="source",
            field=models.CharField(
                choices=[
                    ("plex", "Plex"),
                    ("jellyfin", "Jellyfin"),
                    ("radarr", "Radarr"),
                    ("sonarr", "Sonarr"),
                    ("mylar", "Mylar3"),
                    ("kapowarr", "Kapowarr"),
                    ("bingebandit", "BingeBandit"),
                ],
                max_length=20,
            ),
        ),
    ]
