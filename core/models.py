import re
import secrets
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def new_code():
    # Long random code in the QR link so guests cannot guess other tables.
    return secrets.token_urlsafe(8)


def max_photo_size(f):
    if f.size > 5 * 1024 * 1024:
        raise ValidationError("The photo is larger than 5 MB.")


# Plans restaurants pay for. Limits are enforced when a table or a staff login is added. None = no limit.
PLANS = {
    "starter": {"name": "Starter", "price": 4999, "tables": 10, "staff": 5},
    "pro": {"name": "Pro", "price": 9999, "tables": 40, "staff": 25},
    "enterprise": {"name": "Enterprise", "price": 24999, "tables": None, "staff": None},
}
TRIAL_DAYS = 14


def hex_colour(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
        raise ValidationError("Use a colour like #4c8dff.")


class Restaurant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=60, unique=True, null=True, blank=True, help_text="Used in the booking link")
    tagline = models.CharField(max_length=120, blank=True)
    brand_color = models.CharField(max_length=7, default="#4c8dff", validators=[hex_colour])
    logo = models.ImageField(upload_to="logos/", blank=True, validators=[max_photo_size])
    plan = models.CharField(max_length=12, choices=[(k, v["name"]) for k, v in PLANS.items()], default="pro")
    trial_ends = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    suspended = models.BooleanField(default=False, help_text="Set by the platform; guests cannot order and staff see a notice")
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    currency = models.CharField(max_length=8, default="Rs")
    service_percent = models.DecimalField(max_digits=5, decimal_places=2, default=5, help_text="Service charge added to every bill")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=10, help_text="Tax added to every bill")
    wifi_name = models.CharField(max_length=60, blank=True, help_text="Guest WiFi network shown while guests wait")
    wifi_password = models.CharField(max_length=60, blank=True)
    reward_rate = models.PositiveIntegerField(default=10, help_text="Reward points earned for every 100 spent")
    reward_goal = models.PositiveIntegerField(default=200, help_text="Points needed for the reward")
    reward_text = models.CharField(max_length=80, default="Free Mango Lassi")

    @property
    def initials(self):
        return "".join(w[0] for w in self.name.split())[:2].upper() or "R"

    @property
    def plan_info(self):
        return PLANS[self.plan]

    @property
    def status(self):
        """suspended, active (paid), trial, or expired."""
        today = date.today()
        if self.suspended:
            return "suspended"
        if self.paid_until and self.paid_until >= today:
            return "active"
        if self.trial_ends and self.trial_ends >= today:
            return "trial"
        return "expired"

    @property
    def open_for_business(self):
        return self.status in ("active", "trial")

    @property
    def days_left(self):
        end = self.paid_until if self.status == "active" else self.trial_ends if self.status == "trial" else None
        return (end - date.today()).days if end else 0

    @property
    def brand(self):
        """The accent colour and its lighter and soft versions for CSS."""
        c = self.brand_color if re.fullmatch(r"#[0-9a-fA-F]{6}", self.brand_color or "") else "#4c8dff"
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        light = "#%02x%02x%02x" % tuple(min(255, int(x + (255 - x) * .25)) for x in (r, g, b))
        return {"acc": c, "acc_h": light, "acc_s": f"rgba({r},{g},{b},.14)"}

    def __str__(self):
        return self.name

    @property
    def picture(self):
        """Uploaded photo URL if there is one, otherwise the illustration name."""
        return self.photo.url if self.photo else self.art


class StaffProfile(models.Model):
    OWNER, WAITER, KITCHEN = "owner", "waiter", "kitchen"
    ROLES = [(OWNER, "Owner"), (WAITER, "Waiter"), (KITCHEN, "Kitchen")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLES)

    def __str__(self):
        return f"{self.user} ({self.role}, {self.restaurant})"


class Table(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="tables")
    number = models.PositiveIntegerField()
    code = models.CharField(max_length=32, unique=True, default=new_code, editable=False)
    x = models.FloatField(default=50, help_text="Position on the floor map, 0 to 100 percent from the left")
    y = models.FloatField(default=50, help_text="Position on the floor map, 0 to 100 percent from the top")
    round = models.BooleanField(default=False, help_text="Draw as a round table")

    class Meta:
        unique_together = [("restaurant", "number")]
        ordering = ["number"]

    def __str__(self):
        return f"{self.restaurant} T{self.number}"


class Category(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    @property
    def picture(self):
        """Uploaded photo URL if there is one, otherwise the illustration name."""
        return self.photo.url if self.photo else self.art


class Dish(models.Model):
    SPICE = [("", "-"), ("mild", "Mild"), ("medium", "Medium"), ("hot", "Hot")]
    ART = [
        ("", "None"), ("seekh", "Seekh kebab"), ("tikka", "Tikka"), ("makhani", "Curry bowl (red)"), ("dal", "Dal bowl (yellow)"),
        ("biryani", "Biryani"), ("naan", "Naan"), ("garlic", "Garlic naan"), ("lassi", "Lassi"), ("chai", "Tea"), ("limesoda", "Lime soda"),
    ]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    photo = models.ImageField(upload_to="dishes/", blank=True, validators=[max_photo_size], help_text="Square photo, up to 5 MB. Shown instead of the illustration.")
    allergens = models.CharField(max_length=120, blank=True, help_text="Comma separated, e.g. Dairy, Nuts")
    spice = models.CharField(max_length=6, choices=SPICE, blank=True)
    station = models.CharField(max_length=40, default="Kitchen", help_text="Kitchen column, e.g. Tandoor, Curry, Drinks")
    prep_minutes = models.PositiveIntegerField(default=15)
    available = models.BooleanField(default=True)
    art = models.CharField(max_length=12, blank=True, choices=ART, help_text="Illustration shown on the menu")
    featured = models.BooleanField(default=False, help_text="Shown as the Chef's pick on the menu")
    auto_out = models.BooleanField(default=False, editable=False)  # sold out by inventory, comes back on restock

    class Meta:
        ordering = ["category__position", "name"]
        verbose_name_plural = "dishes"

    def __str__(self):
        return self.name

    @property
    def picture(self):
        """Uploaded photo URL if there is one, otherwise the illustration name."""
        return self.photo.url if self.photo else self.art


class DishSize(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="sizes")
    label = models.CharField(max_length=40)
    price = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.dish} {self.label}"


class Deal(models.Model):
    """A cheaper price on a partner dish when it is ordered with the main dish, for example Lassi for 99 with a curry."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="deals", help_text="The main dish")
    partner = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="+", help_text="The dish that gets the deal price")
    price = models.DecimalField(max_digits=8, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.partner} at {self.price} with {self.dish}"


class Guest(models.Model):
    """One person at a table. Two guests can share a table and a bill."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="guests")
    name = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} at T{self.table.number}"


class Member(models.Model):
    """A guest who joined the rewards by email. Points stay with the email, not the table."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    email = models.EmailField()
    points = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("restaurant", "email")]

    def __str__(self):
        return f"{self.email} ({self.points})"


class Bill(models.Model):
    """Everything ordered at a table by one party. Closes when it is paid, which frees the table."""

    OPEN, PAYING, PAID = "open", "paying", "paid"
    STATUSES = [(OPEN, "Open"), (PAYING, "Paying"), (PAID, "Paid")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="bills")
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="bills")
    status = models.CharField(max_length=8, choices=STATUSES, default=OPEN)
    split = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Bill {self.pk} T{self.table.number} {self.status}"


class Order(models.Model):
    PLACED, COOKING, READY, SERVED, CANCELLED = "placed", "cooking", "ready", "served", "cancelled"
    STATUSES = [(PLACED, "Placed"), (COOKING, "Cooking"), (READY, "Ready"), (SERVED, "Served"), (CANCELLED, "Cancelled")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="orders")
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="orders")
    bill = models.ForeignKey(Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    guest = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    status = models.CharField(max_length=10, choices=STATUSES, default=PLACED)
    created_at = models.DateTimeField(auto_now_add=True)
    cooking_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(max_length=60, blank=True, help_text="Who cancelled it: a staff username or 'guest'")
    stock_taken = models.BooleanField(default=False, editable=False)  # ingredients were deducted when it went to the kitchen

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Order {self.pk} T{self.table.number} {self.status}"

    @property
    def total(self):
        return sum(i.unit_price * i.quantity for i in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    dish = models.ForeignKey(Dish, on_delete=models.PROTECT)
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True)
    size_label = models.CharField(max_length=40, blank=True)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField()
    note = models.CharField(max_length=140, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)


class ServiceRequest(models.Model):
    WATER, NAPKINS, BILL, CALL = "water", "napkins", "bill", "call"
    KINDS = [(WATER, "Water"), (NAPKINS, "Napkins"), (BILL, "Bill"), (CALL, "Call waiter")]
    OPEN, ACKNOWLEDGED, DONE = "open", "acknowledged", "done"
    STATUSES = [(OPEN, "Open"), (ACKNOWLEDGED, "On the way"), (DONE, "Done")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="requests")
    kind = models.CharField(max_length=10, choices=KINDS)
    status = models.CharField(max_length=14, choices=STATUSES, default=OPEN)
    assigned = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class Payment(models.Model):
    """One person's share of a bill. The guest picks how to pay and the waiter confirms at the table. No money moves here."""

    CASH, BANK, CARD, APPLE, GOOGLE = "cash", "bank", "card", "apple", "google"
    METHODS = [(CASH, "Cash"), (BANK, "Bank transfer"), (CARD, "Card"), (APPLE, "Apple Pay"), (GOOGLE, "Google Pay")]
    REQUESTED, PAID = "requested", "paid"

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payments")
    guest = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True)
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True)
    method = models.CharField(max_length=8, choices=METHODS)
    email = models.EmailField(blank=True)
    share = models.DecimalField(max_digits=10, decimal_places=2)
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, default=REQUESTED)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)


class Review(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="reviews")
    guest = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True)
    stars = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class QuizQuestion(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    text = models.CharField(max_length=200)
    options = models.JSONField(help_text="A list of answer choices")
    answer = models.PositiveSmallIntegerField(help_text="Position of the right choice, starting at 0")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.text


class QuizAnswer(models.Model):
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE)
    choice = models.PositiveSmallIntegerField()
    correct = models.BooleanField()

    class Meta:
        unique_together = [("guest", "question")]


class PlanPayment(models.Model):
    """A plan payment, recorded by the platform today; a payment gateway would call the same service later."""
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="plan_payments")
    plan = models.CharField(max_length=12)
    months = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    recorded_by = models.CharField(max_length=150, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    period_end = models.DateField()

    class Meta:
        ordering = ["-paid_at"]


class Reservation(models.Model):
    REQUESTED, CONFIRMED, SEATED, CANCELLED, NO_SHOW = "requested", "confirmed", "seated", "cancelled", "no_show"
    STATUSES = [(REQUESTED, "Requested"), (CONFIRMED, "Confirmed"), (SEATED, "Seated"), (CANCELLED, "Cancelled"), (NO_SHOW, "No-show")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="reservations")
    name = models.CharField(max_length=80)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    party = models.PositiveIntegerField()
    when = models.DateTimeField()
    note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=REQUESTED)
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True, related_name="reservations")
    code = models.CharField(max_length=16, default=new_code, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["when"]

    def __str__(self):
        return f"{self.name} x{self.party} {self.when:%d %b %H:%M}"


class Ingredient(models.Model):
    UNITS = [("g", "grams"), ("ml", "millilitres"), ("pcs", "pieces")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="ingredients")
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=3, choices=UNITS, default="g")
    stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    low_at = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Warn the kitchen and owner at or below this")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["restaurant", "name"], name="one_ingredient_name_per_restaurant")]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class RecipeItem(models.Model):
    """How much of an ingredient one portion of a dish uses."""
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="recipe")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="uses")
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.amount} {self.ingredient.unit} {self.ingredient.name}"
