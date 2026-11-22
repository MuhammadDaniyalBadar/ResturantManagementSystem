import os
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from core import services
from core.models import Category, Deal, Dish, DishSize, QuizQuestion, Restaurant, StaffProfile, Table

# (category, name, description, price, spice, allergens, station, prep minutes, [(size, price)], art)
MENU = [
    ("Starters", "Seekh Kebab", "Minced lamb skewers from the clay oven", 450, "medium", "", "Tandoor", 15, [], "seekh"),
    ("Starters", "Paneer Tikka", "Marinated cottage cheese, charred", 380, "mild", "Dairy", "Tandoor", 14, [], "tikka"),
    ("Mains", "Paneer Makhani", "Paneer in a rich tomato and butter gravy", 520, "medium", "Dairy, Nuts", "Curry", 18, [("Half", 520), ("Full", 880)], "makhani"),
    ("Mains", "Dal Tadka", "Yellow lentils with a garlic tempering", 340, "mild", "Dairy", "Curry", 12, [("Half", 340), ("Full", 600)], "dal"),
    ("Mains", "Veg Biryani", "Fragrant rice layered with vegetables", 420, "hot", "Dairy", "Curry", 20, [], "biryani"),
    ("Breads", "Butter Naan", "Soft naan brushed with butter", 80, "", "Dairy, Gluten", "Tandoor", 8, [], "naan"),
    ("Breads", "Garlic Naan", "Naan with garlic and coriander", 100, "", "Dairy, Gluten", "Tandoor", 8, [], "garlic"),
    ("Drinks", "Mango Lassi", "Chilled yoghurt and mango", 180, "", "Dairy", "Drinks", 4, [], "lassi"),
    ("Drinks", "Masala Chai", "Spiced milk tea", 90, "", "Dairy", "Drinks", 5, [], "chai"),
    ("Drinks", "Fresh Lime Soda", "Sweet or salted", 120, "", "", "Drinks", 3, [], "limesoda"),
]
# (main dish, partner dish, deal price)
DEALS = [("Paneer Makhani", "Mango Lassi", 99), ("Veg Biryani", "Mango Lassi", 99), ("Dal Tadka", "Butter Naan", 60)]
# (x, y, round) positions on the floor map, in percent
LAYOUT = [(18, 28, False), (50, 28, True), (82, 28, False), (18, 70, True), (50, 70, False), (82, 70, True)]
QUIZ = [
    ("Which is the most expensive spice in the world?", ["Saffron", "Cardamom", "Cinnamon", "Cumin"], 0),
    ("Paneer is made from…", ["Milk", "Soya beans", "Chickpeas", "Rice"], 0),
    ("Naan is traditionally baked in a…", ["Tandoor", "Steamer", "Pressure cooker", "Microwave"], 0),
    ("Lassi is a drink made from…", ["Yoghurt", "Coconut", "Tea leaves", "Sugar cane"], 0),
    ("Which of these is a lentil dish?", ["Gulab jamun", "Dal tadka", "Seekh kebab", "Raita"], 1),
    ("Which compound makes chilli peppers hot?", ["Caffeine", "Curcumin", "Capsaicin", "Gluten"], 2),
    ("Garam masala is a…", ["Cooling drink", "Hot spice blend", "Fried bread", "Sweet sauce"], 1),
    ("Chai is the word for…", ["Rice", "Bread", "Water", "Tea"], 3),
    ("Seekh means…", ["Soup", "Spice", "Skewer", "Bread"], 2),
    ("Which of these is a flatbread?", ["Biryani", "Korma", "Kheer", "Naan"], 3),
]


class Command(BaseCommand):
    help = "Create a demo restaurant with a menu, six tables, deals, a quiz and four staff logins."

    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if a restaurant already exists.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts["if_empty"] and Restaurant.objects.exists():
            self.stdout.write("Data already present, skipping.")
            return
        password = os.environ.get("DEMO_PASSWORD", "demo1234")
        restaurant = Restaurant.objects.create(
            name="Demo Kitchen", currency="Rs", wifi_name="DemoKitchen-Guest", wifi_password="spice2026",
            reward_rate=10, reward_goal=200, reward_text="Free Mango Lassi",
            slug="demo-kitchen", tagline="Spice, smoke and tandoor", plan="pro", paid_until=date.today() + timedelta(days=365),
        )

        categories, dishes = {}, {}
        for cat, name, desc, price, spice, allergens, station, prep, sizes, art in MENU:
            if cat not in categories:
                categories[cat] = Category.objects.create(restaurant=restaurant, name=cat, position=len(categories))
            dish = dishes[name] = Dish.objects.create(
                restaurant=restaurant, category=categories[cat], name=name, description=desc, price=price,
                spice=spice, allergens=allergens, station=station, prep_minutes=prep, art=art, featured=(art == "makhani"),
            )
            for label, size_price in sizes:
                DishSize.objects.create(dish=dish, label=label, price=size_price)
        for main, partner, price in DEALS:
            Deal.objects.create(restaurant=restaurant, dish=dishes[main], partner=dishes[partner], price=price)

        for number, (x, y, round_) in enumerate(LAYOUT, start=1):
            Table.objects.create(restaurant=restaurant, number=number, x=x, y=y, round=round_)
        for position, (text, options, answer) in enumerate(QUIZ):
            QuizQuestion.objects.create(restaurant=restaurant, text=text, options=options, answer=answer, position=position)

        for username, first, role in (("owner", "Ayesha", StaffProfile.OWNER), ("waiter", "Aman", StaffProfile.WAITER), ("sara", "Sara", StaffProfile.WAITER), ("kitchen", "Bilal", StaffProfile.KITCHEN)):
            user = User.objects.create_user(username, password=password, first_name=first)
            StaffProfile.objects.create(user=user, restaurant=restaurant, role=role)
            services.set_access(user, role)

        if not User.objects.filter(username="platform").exists():  # you, running the platform: sees every restaurant at /platform/
            User.objects.create_superuser("platform", password=password)
        self.stdout.write(self.style.SUCCESS("Demo restaurant created."))
        self.stdout.write(f"Logins: owner, waiter, sara, kitchen, and platform (the whole platform). Password: {password if not os.environ.get('DEMO_PASSWORD') else '(from DEMO_PASSWORD)'}")
        for table in restaurant.tables.all():
            self.stdout.write(f"  Table {table.number}: /t/{table.code}/")
