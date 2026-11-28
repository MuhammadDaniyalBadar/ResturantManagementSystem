from django.db import migrations


def add_pairings(apps, schema_editor):
    # restaurants that already have the built-in menu get its "Goes well with" suggestions once
    from core.menu import apply_pairings
    Restaurant, Dish = apps.get_model("core", "Restaurant"), apps.get_model("core", "Dish")
    for r in Restaurant.objects.filter(menu_version__startswith="2026-11-saffron"):
        apply_pairings(r, dish_model=Dish)


class Migration(migrations.Migration):
    dependencies = [("core", "0011_dish_pairs")]
    operations = [migrations.RunPython(add_pairings, migrations.RunPython.noop)]
