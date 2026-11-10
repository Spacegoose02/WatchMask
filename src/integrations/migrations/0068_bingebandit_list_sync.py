from django.db import migrations, models


def _column_exists(schema_editor, table_name, column_name):
    """Return True when a database column already exists."""
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor,
            table_name,
        )
    return column_name in {getattr(column, "name", column[0]) for column in description}


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
        ("integrations", "0067_lootden"),
    ]

    operations = [
        AddFieldIfNotExists(
            model_name="bingebanditaccount",
            name="sync_lists",
            field=models.BooleanField(
                default=False,
                help_text="Mirror the BingeBandit watchlist and playlists into Watchmask lists",
            ),
        ),
    ]
