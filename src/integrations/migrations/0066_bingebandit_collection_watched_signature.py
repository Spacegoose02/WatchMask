from django.db import migrations, models


def _column_exists(schema_editor, table_name, column_name):
    """Return True when a database column already exists."""
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor,
            table_name,
        )
    return column_name in {getattr(column, "name", column[0]) for column in description}


def _constraint_exists(schema_editor, table_name, constraint_name):
    """Return True when the table already has the named constraint."""
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor,
            table_name,
        )
    return constraint_name in constraints


class AddFieldIfNotExists(migrations.AddField):
    """Add a field only when its column doesn't already exist."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        to_model = to_state.apps.get_model(app_label, self.model_name)
        field = to_model._meta.get_field(self.name)
        if _column_exists(schema_editor, to_model._meta.db_table, field.column):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0065_bingebandit_library_name"),
    ]

    operations = [
        AddFieldIfNotExists(
            model_name="bingebanditaccount",
            name="webhook_secret",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Encrypted secret BingeBandit signs webhooks with; empty skips the check",
            ),
        ),
        AddFieldIfNotExists(
            model_name="bingebanditaccount",
            name="sync_collection",
            field=models.BooleanField(
                default=True,
                help_text="Mark the movies and shows on the server as collected",
            ),
        ),
        AddFieldIfNotExists(
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
