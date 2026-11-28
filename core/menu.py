"""The built-in menu for the showcase restaurant: a Lahore-style family restaurant, priced in rupees (2026).

apply_menu() loads it once per MENU_VERSION. It adds or updates dishes by name and never deletes one,
because past orders point at their dishes. Edits an owner makes later are kept until MENU_VERSION changes.
"""
from django.db import transaction

from .models import Category, Deal, Dish, DishSize

MENU_VERSION = "2026-11-saffron-1"

RESTAURANT = {
    "name": "Saffron House",
    "tagline": "Lahori karahi, charcoal grill and fresh tandoor. 100% halal.",
    "wifi_name": "SaffronHouse-Guest",
    "wifi_password": "karahi2026",
    "reward_text": "Free Kheer",
    "reward_goal": 300,
}

# station -> the kitchen screen column that cooks it
GRILL, TANDOOR, KARAHI, KITCHEN, DRINKS, DESSERTS = "Grill", "Tandoor", "Karahi", "Kitchen", "Drinks", "Desserts"


def d(name, desc, price, *, sizes=(), spice="", allergens="", station=KITCHEN, prep=15, art="", diet="nonveg", serves=None, badge="", featured=False):
    return dict(name=name, description=desc, price=price, sizes=sizes, spice=spice, allergens=allergens, station=station,
                prep_minutes=prep, art=art, diet=diet, serves=serves, badge=badge, featured=featured)


MENU = [
    ("Soups", [
        d("Chicken Corn Soup", "Silky chicken broth with sweet corn, shredded chicken and egg ribbons. Vinegar and chilli sauce on the side.", 480,
          sizes=(("Bowl", 480), ("Family (3-4)", 1250)), allergens="Egg", prep=10, art="dal", serves=1, badge="bestseller"),
        d("Hot & Sour Soup", "Tangy, peppery soup with chicken, mushrooms, carrots and tofu.", 520,
          sizes=(("Bowl", 520), ("Family (3-4)", 1350)), spice="hot", allergens="Egg, Soy", prep=10, art="dal", serves=1),
    ]),
    ("Starters", [
        d("Chicken Pakora", "Boneless chicken in a spiced gram-flour batter, fried crisp. Served with mint chutney. 8 pieces.", 690,
          spice="medium", allergens="Gluten", prep=12, art="tikka", serves=2),
        d("Lahori Fish Pakora", "River sole marinated in ajwain, red chilli and lemon, fried the old-Lahore way. 400 g.", 1490,
          spice="medium", allergens="Fish", prep=15, art="tikka", serves=2, badge="bestseller"),
        d("Vegetable Samosa", "Crisp pastry filled with spiced potato and peas. Tamarind and mint chutney. 4 pieces.", 420,
          allergens="Gluten", prep=8, art="tikka", diet="veg", serves=2),
        d("Dahi Bhallay", "Soft lentil dumplings in chilled whipped yoghurt with tamarind chutney and chaat masala.", 480,
          allergens="Dairy", prep=5, art="dal", diet="veg", serves=1),
        d("Paneer Tikka", "Cottage cheese, peppers and onion marinated in tandoori spices and charred in the clay oven. 6 pieces.", 1290,
          spice="mild", allergens="Dairy", station=TANDOOR, prep=14, art="tikka", diet="veg", serves=2),
    ]),
    ("BBQ & Grill", [
        d("Chicken Tikka", "A whole leg or breast quarter marinated overnight and roasted over charcoal.", 620,
          sizes=(("Leg piece", 620), ("Chest piece", 650)), spice="medium", allergens="Dairy", station=GRILL, prep=18, art="tikka", serves=1, badge="bestseller"),
        d("Malai Boti", "Boneless chicken in cream, cheese and white pepper, grilled until just charred. 10 pieces.", 1490,
          spice="mild", allergens="Dairy", station=GRILL, prep=16, art="tikka", serves=2),
        d("Seekh Kebab", "Minced mutton with ginger, green chilli and fresh coriander, grilled on skewers. 4 skewers.", 1650,
          spice="medium", station=GRILL, prep=15, art="seekh", serves=2),
        d("Reshmi Kebab", "Mild, silky chicken mince kebabs with cream and cashew. 4 skewers.", 1390,
          spice="mild", allergens="Dairy, Nuts", station=GRILL, prep=15, art="seekh", serves=2),
        d("Peshawari Chapli Kebab", "Flat beef patties with tomato, pomegranate seed and crushed coriander, fried in their own fat. 2 pieces.", 790,
          spice="hot", station=GRILL, prep=12, art="seekh", serves=1),
        d("Mixed Grill Platter", "Chicken tikka, malai boti, seekh and reshmi kebab with grilled vegetables, raita and two naan.", 4290,
          spice="medium", allergens="Dairy, Gluten, Nuts", station=GRILL, prep=25, art="seekh", serves=4, badge="chef"),
    ]),
    ("Karahi & Handi", [
        d("Chicken Karahi", "Bone-in chicken cooked in a blazing wok with tomato, green chilli, ginger and butter. Our signature.", 1790,
          sizes=(("Half", 1790), ("Full", 3390)), spice="hot", allergens="Dairy", station=KARAHI, prep=22, art="makhani", serves=2, badge="bestseller"),
        d("Mutton Karahi", "Tender mutton on the bone, tomato and black pepper, finished with fresh ginger.", 2990,
          sizes=(("Half", 2990), ("Full", 5690)), spice="hot", allergens="Dairy", station=KARAHI, prep=30, art="makhani", serves=2),
        d("Chicken White Handi", "Boneless chicken in a rich cream, yoghurt and white-pepper gravy, cooked in a clay pot.", 1890,
          sizes=(("Half", 1890), ("Full", 3490)), spice="mild", allergens="Dairy, Nuts", station=KARAHI, prep=20, art="dal", serves=2),
        d("Butter Chicken", "Tandoori chicken simmered in a smooth tomato, butter and fenugreek sauce.", 1690,
          sizes=(("Half", 1690), ("Full", 3150)), spice="mild", allergens="Dairy, Nuts", station=KARAHI, prep=18, art="makhani", serves=2),
        d("Paneer Makhani", "Soft paneer in a slow-cooked tomato and butter gravy with a touch of cream.", 1390,
          sizes=(("Half", 1390), ("Full", 2490)), spice="medium", allergens="Dairy, Nuts", station=KARAHI, prep=18, art="makhani", diet="veg", serves=2, featured=True),
        d("Palak Paneer", "Paneer in a garlicky spinach gravy, finished with butter.", 1290,
          sizes=(("Half", 1290), ("Full", 2350)), spice="mild", allergens="Dairy", station=KARAHI, prep=16, art="dal", diet="veg", serves=2),
        d("Dal Makhani", "Black lentils slow-cooked overnight with butter and cream.", 990,
          sizes=(("Half", 990), ("Full", 1790)), spice="mild", allergens="Dairy", station=KARAHI, prep=10, art="dal", diet="veg", serves=2),
        d("Dal Tadka", "Yellow lentils tempered with garlic, cumin and dried red chilli.", 890,
          sizes=(("Half", 890), ("Full", 1590)), spice="medium", station=KARAHI, prep=10, art="dal", diet="vegan", serves=2),
    ]),
    ("Lahori Classics", [
        d("Beef Nihari", "Beef shank slow-cooked for eight hours in a spiced, silky gravy. With ginger, chilli and lemon on the side.", 1190,
          spice="hot", station=KITCHEN, prep=8, art="makhani", serves=1, badge="bestseller"),
        d("Haleem", "Wheat, lentils and shredded beef cooked down for hours, topped with fried onion and fresh ginger.", 950,
          spice="medium", allergens="Gluten", station=KITCHEN, prep=8, art="dal", serves=1),
        d("Mutton Paya", "Trotters in a rich, gelatinous broth. A weekend breakfast favourite.", 1290,
          spice="medium", station=KITCHEN, prep=10, art="makhani", serves=1),
    ]),
    ("Rice", [
        d("Chicken Biryani", "Aged basmati layered with spiced chicken, saffron and fried onion. Served with raita.", 990,
          spice="hot", allergens="Dairy", station=KITCHEN, prep=10, art="biryani", serves=1, badge="bestseller"),
        d("Mutton Pulao", "Fragrant yakhni pulao with tender mutton and whole spices.", 1490,
          spice="mild", station=KITCHEN, prep=12, art="biryani", serves=1),
        d("Veg Biryani", "Basmati layered with seasonal vegetables, mint and saffron.", 850,
          spice="hot", allergens="Dairy", station=KITCHEN, prep=12, art="biryani", diet="veg", serves=1),
        d("Steamed Rice", "Fluffy long-grain basmati.", 450, station=KITCHEN, prep=5, art="biryani", diet="vegan", serves=2),
    ]),
    ("Breads", [
        d("Plain Naan", "Hand-stretched and baked to order in the tandoor.", 150, allergens="Gluten, Dairy", station=TANDOOR, prep=6, art="naan", diet="veg", serves=1),
        d("Butter Naan", "Our plain naan, brushed with melted butter.", 220, allergens="Gluten, Dairy", station=TANDOOR, prep=6, art="naan", diet="veg", serves=1, badge="bestseller"),
        d("Garlic Naan", "Topped with garlic, butter and fresh coriander.", 290, allergens="Gluten, Dairy", station=TANDOOR, prep=7, art="garlic", diet="veg", serves=1),
        d("Roghni Naan", "Soft, slightly sweet naan with sesame seeds.", 240, allergens="Gluten, Dairy, Sesame", station=TANDOOR, prep=7, art="naan", diet="veg", serves=1),
        d("Cheese Naan", "Stuffed with melted mozzarella and cheddar.", 490, allergens="Gluten, Dairy", station=TANDOOR, prep=8, art="garlic", diet="veg", serves=1, badge="new"),
        d("Tandoori Roti", "Whole-wheat flatbread from the clay oven.", 90, allergens="Gluten", station=TANDOOR, prep=5, art="naan", diet="vegan", serves=1),
        d("Lachha Paratha", "Flaky, layered whole-wheat paratha.", 190, allergens="Gluten, Dairy", station=TANDOOR, prep=7, art="naan", diet="veg", serves=1),
    ]),
    ("Sides", [
        d("Mint Raita", "Whipped yoghurt with mint, cucumber and roasted cumin.", 220, allergens="Dairy", prep=3, art="lassi", diet="veg", serves=2),
        d("Kachumber Salad", "Diced tomato, cucumber and onion with lemon and green chilli.", 320, spice="mild", prep=4, art="dal", diet="vegan", serves=2),
        d("Masala Fries", "Crisp fries tossed in chaat masala.", 450, spice="medium", prep=8, art="naan", diet="vegan", serves=2),
    ]),
    ("Desserts", [
        d("Kheer", "Slow-cooked rice pudding with cardamom, pistachio and almond.", 490, allergens="Dairy, Nuts", station=DESSERTS, prep=3, art="lassi", diet="veg", serves=1, badge="bestseller"),
        d("Gulab Jamun", "Warm milk dumplings in rose and cardamom syrup. 2 pieces.", 390, allergens="Dairy, Gluten", station=DESSERTS, prep=3, art="chai", diet="veg", serves=1),
        d("Gajar Halwa", "Winter carrots slow-cooked in milk and ghee with nuts. Served warm.", 690, allergens="Dairy, Nuts", station=DESSERTS, prep=4, art="makhani", diet="veg", serves=1, badge="seasonal"),
        d("Kulfi Falooda", "Pistachio kulfi with vermicelli, basil seeds and rose syrup.", 790, allergens="Dairy, Nuts", station=DESSERTS, prep=5, art="lassi", diet="veg", serves=1),
    ]),
    ("Drinks", [
        d("Mango Lassi", "Chilled yoghurt blended with Sindhri mango.", 450, allergens="Dairy", station=DRINKS, prep=4, art="lassi", diet="veg", serves=1),
        d("Lassi", "Thick, chilled yoghurt drink.", 380, sizes=(("Sweet", 380), ("Salted", 380)), allergens="Dairy", station=DRINKS, prep=4, art="lassi", diet="veg", serves=1),
        d("Mint Margarita", "Fresh mint, lemon and soda, blended with ice.", 490, station=DRINKS, prep=4, art="limesoda", diet="vegan", serves=1, badge="bestseller"),
        d("Fresh Lime Soda", "Fresh lime with soda.", 380, sizes=(("Sweet", 380), ("Salted", 380)), station=DRINKS, prep=3, art="limesoda", diet="vegan", serves=1),
        d("Soft Drink", "Chilled can.", 250, station=DRINKS, prep=1, art="limesoda", diet="vegan", serves=1),
        d("Mineral Water", "Still water.", 120, sizes=(("Small", 120), ("Large", 220)), station=DRINKS, prep=1, art="limesoda", diet="vegan", serves=1),
        d("Masala Chai", "Strong tea brewed with milk, cardamom, ginger and cinnamon.", 290, allergens="Dairy", station=DRINKS, prep=5, art="chai", diet="veg", serves=1),
        d("Doodh Patti", "Tea leaves simmered in full-cream milk, Pakistani style.", 260, allergens="Dairy", station=DRINKS, prep=5, art="chai", diet="veg", serves=1),
        d("Kashmiri Chai", "Pink tea with pistachio and almond.", 450, allergens="Dairy, Nuts", station=DRINKS, prep=6, art="chai", diet="veg", serves=1),
    ]),
]

# (main dish, add-on, add-on price with the main) shown as "add X for Rs Y" on the main dish
DEALS = [
    ("Paneer Makhani", "Mango Lassi", 299),
    ("Chicken Karahi", "Garlic Naan", 199),
    ("Chicken Biryani", "Mint Raita", 99),
    ("Mixed Grill Platter", "Mint Margarita", 349),
    ("Beef Nihari", "Plain Naan", 99),
]


@transaction.atomic
def apply_menu(restaurant, force=False):
    """Load the built-in menu into this restaurant once per MENU_VERSION. Returns True if it loaded."""
    if restaurant.menu_version == MENU_VERSION and not force:
        return False
    if restaurant.name in ("Demo Kitchen", ""):  # only rename the showcase restaurant, never a real one
        for k, v in RESTAURANT.items():
            setattr(restaurant, k, v)
    names = {item["name"] for _, items in MENU for item in items}
    by_name = {x.name: x for x in Dish.objects.filter(restaurant=restaurant)}
    for position, (cat_name, items) in enumerate(MENU):
        cat, _ = Category.objects.get_or_create(restaurant=restaurant, name=cat_name)
        cat.position = position
        cat.save(update_fields=["position"])
        for item in items:
            item = dict(item)
            sizes = item.pop("sizes")
            dish = by_name.get(item["name"]) or Dish(restaurant=restaurant, name=item["name"])
            for k, v in item.items():
                setattr(dish, k, v)
            dish.category, dish.available, dish.retired = cat, True, False
            dish.save()
            by_name[dish.name] = dish
            dish.sizes.all().delete()  # sizes are copied onto order lines, so replacing them is safe
            for label, price in sizes:
                DishSize.objects.create(dish=dish, label=label, price=price)
    # dishes from an older built-in menu that are no longer on it: hidden from guests, kept for past orders
    Dish.objects.filter(restaurant=restaurant).exclude(name__in=names).update(available=False, featured=False, retired=True)
    Category.objects.filter(restaurant=restaurant, dish__isnull=True).delete()
    Deal.objects.filter(restaurant=restaurant).update(active=False)  # old deals stay for past orders, off the menu
    for main, partner, price in DEALS:
        Deal.objects.update_or_create(restaurant=restaurant, dish=by_name[main], partner=by_name[partner], defaults={"price": price, "active": True})
    restaurant.menu_version = MENU_VERSION
    restaurant.save()
    return True
