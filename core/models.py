import secrets

from django.conf import settings
from django.db import models


def new_code():
    # Long random code in the QR link so guests cannot guess other tables.
    return secrets.token_urlsafe(8)


class Restaurant(models.Model):
    name = models.CharField(max_length=120)
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

    def __str__(self):
        return self.name


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
    # shortcut: a photo link, not an upload, so Alpha needs no image library or file storage.
    image_url = models.URLField(blank=True)
    allergens = models.CharField(max_length=120, blank=True, help_text="Comma separated, e.g. Dairy, Nuts")
    spice = models.CharField(max_length=6, choices=SPICE, blank=True)
    station = models.CharField(max_length=40, default="Kitchen", help_text="Kitchen column, e.g. Tandoor, Curry, Drinks")
    prep_minutes = models.PositiveIntegerField(default=15)
    available = models.BooleanField(default=True)
    art = models.CharField(max_length=12, blank=True, choices=ART, help_text="Illustration shown on the menu")
    featured = models.BooleanField(default=False, help_text="Shown as the Chef's pick on the menu")

    class Meta:
        ordering = ["category__position", "name"]
        verbose_name_plural = "dishes"

    def __str__(self):
        return self.name


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
    PLACED, COOKING, READY, SERVED = "placed", "cooking", "ready", "served"
    STATUSES = [(PLACED, "Placed"), (COOKING, "Cooking"), (READY, "Ready"), (SERVED, "Served")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="orders")
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="orders")
    bill = models.ForeignKey(Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    guest = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    status = models.CharField(max_length=10, choices=STATUSES, default=PLACED)
    created_at = models.DateTimeField(auto_now_add=True)
    cooking_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)

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
