import os

from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Category, Dish, DishSize, Restaurant, StaffProfile, Table

# (category, name, description, price, spice, allergens, station, prep minutes, [(size, price)])
MENU = [
    ("Starters", "Seekh Kebab", "Minced lamb skewers from the clay oven", 450, "medium", "", "Tandoor", 15, []),
    ("Starters", "Paneer Tikka", "Marinated cottage cheese, charred", 380, "mild", "Dairy", "Tandoor", 14, []),
    ("Mains", "Paneer Makhani", "Paneer in a rich tomato and butter gravy", 520, "medium", "Dairy, Nuts", "Curry", 18, [("Half", 520), ("Full", 880)]),
    ("Mains", "Dal Tadka", "Yellow lentils with a garlic tempering", 340, "mild", "Dairy", "Curry", 12, [("Half", 340), ("Full", 600)]),
    ("Mains", "Veg Biryani", "Fragrant rice layered with vegetables", 420, "medium", "Dairy", "Curry", 20, []),
    ("Breads", "Butter Naan", "Soft naan brushed with butter", 80, "", "Dairy, Gluten", "Tandoor", 8, []),
    ("Breads", "Garlic Naan", "Naan with garlic and coriander", 100, "", "Dairy, Gluten", "Tandoor", 8, []),
    ("Drinks", "Mango Lassi", "Chilled yoghurt and mango", 180, "", "Dairy", "Drinks", 4, []),
    ("Drinks", "Masala Chai", "Spiced milk tea", 90, "", "Dairy", "Drinks", 5, []),
    ("Drinks", "Fresh Lime Soda", "Sweet or salted", 120, "", "", "Drinks", 3, []),
]


class Command(BaseCommand):
    help = "Create a demo restaurant with a menu, six tables and three staff logins."

    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if a restaurant already exists.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts["if_empty"] and Restaurant.objects.exists():
            self.stdout.write("Data already present, skipping.")
            return
        password = os.environ.get("DEMO_PASSWORD", "demo1234")
        restaurant = Restaurant.objects.create(name="Demo Kitchen", currency="Rs")

        categories = {}
        for position, (cat, name, desc, price, spice, allergens, station, prep, sizes) in enumerate(MENU):
            if cat not in categories:
                categories[cat] = Category.objects.create(restaurant=restaurant, name=cat, position=len(categories))
            dish = Dish.objects.create(
                restaurant=restaurant, category=categories[cat], name=name, description=desc, price=price,
                spice=spice, allergens=allergens, station=station, prep_minutes=prep,
            )
            for label, size_price in sizes:
                DishSize.objects.create(dish=dish, label=label, price=size_price)

        for number in range(1, 7):
            Table.objects.create(restaurant=restaurant, number=number)

        for username, role in (("owner", StaffProfile.OWNER), ("waiter", StaffProfile.WAITER), ("kitchen", StaffProfile.KITCHEN)):
            user = User.objects.create_user(username, password=password, is_staff=(role == StaffProfile.OWNER))
            StaffProfile.objects.create(user=user, restaurant=restaurant, role=role)
            if role == StaffProfile.OWNER:  # the owner edits the menu and tables in the admin
                user.user_permissions.add(*Permission.objects.filter(content_type__app_label="core"))

        self.stdout.write(self.style.SUCCESS("Demo restaurant created."))
        self.stdout.write(f"Logins: owner, waiter, kitchen. Password: {password if not os.environ.get('DEMO_PASSWORD') else '(from DEMO_PASSWORD)'}")
        for table in restaurant.tables.all():
            self.stdout.write(f"  Table {table.number}: /t/{table.code}/")
