"""Join Watchmask's Cinemask/BingeBandit migrations to Floppy's chain.

Watchmask branched from Floppy at integrations 0015, so its 0016-0018 run
beside Floppy's 0016-0063. Keeping their names lets existing Watchmask
databases, which already applied them, upgrade without re-creating tables.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0018_bingebandit_library_resume"),
        ("integrations", "0063_wetrakr_account"),
    ]

    operations = []
