from django.db import migrations


def karahi_is_chefs_pick(apps, schema_editor):
    # restaurants that got the built-in menu: the Chef's pick banner moves to Chicken Karahi, which has a real photo
    Dish = apps.get_model("core", "Dish")
    for dish in Dish.objects.filter(name="Paneer Makhani", featured=True, restaurant__menu_version__startswith="2026-11-saffron"):
        if Dish.objects.filter(restaurant=dish.restaurant, name="Chicken Karahi").update(featured=True):
            dish.featured = False
            dish.save(update_fields=["featured"])


class Migration(migrations.Migration):
    dependencies = [("core", "0009_dish_details")]
    operations = [migrations.RunPython(karahi_is_chefs_pick, migrations.RunPython.noop)]
