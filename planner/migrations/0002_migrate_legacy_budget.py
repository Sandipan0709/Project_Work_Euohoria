"""Carry the old single monthly limit over as the overall monthly budget."""
from django.db import migrations


def forwards(apps, schema_editor):
    LegacyBudget = apps.get_model("core", "Budget")
    Budget = apps.get_model("planner", "Budget")
    for legacy in LegacyBudget.objects.filter(monthly_limit__gt=0):
        Budget.objects.get_or_create(
            user_id=legacy.user_id, category=None, period="monthly",
            defaults={"amount": legacy.monthly_limit, "warn_percent": 80},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_profile_min_buffer_profile_notify_bills_and_more"),
        ("planner", "0001_initial"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
