import secrets

from django.conf import settings
from django.db import models


def new_code():
    # Long random code in the QR link so guests cannot guess other tables.
    return secrets.token_urlsafe(8)


class Restaurant(models.Model):
    name = models.CharField(max_length=120)
    currency = models.CharField(max_length=8, default="Rs")

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


class Order(models.Model):
    PLACED, COOKING, READY, SERVED = "placed", "cooking", "ready", "served"
    STATUSES = [(PLACED, "Placed"), (COOKING, "Cooking"), (READY, "Ready"), (SERVED, "Served")]

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="orders")
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="orders")
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
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
