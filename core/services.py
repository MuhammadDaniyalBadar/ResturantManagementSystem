"""All business rules live here: prices, status changes, who may do what.
Views and consumers only call these and display the result."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import Dish, Order, OrderItem, ServiceRequest, StaffProfile

MAX_LINES, MAX_QTY = 30, 20

NEXT = {Order.PLACED: Order.COOKING, Order.COOKING: Order.READY, Order.READY: Order.SERVED}
WHO = {
    Order.COOKING: {StaffProfile.WAITER, StaffProfile.OWNER},
    Order.READY: {StaffProfile.KITCHEN, StaffProfile.OWNER},
    Order.SERVED: {StaffProfile.WAITER, StaffProfile.OWNER},
}


class OrderError(ValueError):
    """A request the guest or staff member can fix; the message is safe to show."""


def notify(restaurant_id, table_id):
    """Tell staff, kitchen and the table's guests to refresh. Sent after the database commit."""
    layer = get_channel_layer()

    def send():
        for group in (f"rest-{restaurant_id}", f"table-{table_id}"):
            async_to_sync(layer.group_send)(group, {"type": "changed"})

    transaction.on_commit(send)


@transaction.atomic
def place_order(table, lines):
    if not isinstance(lines, list) or not lines:
        raise OrderError("Your cart is empty.")
    if len(lines) > MAX_LINES:
        raise OrderError("Too many items in one order.")
    order = Order.objects.create(restaurant=table.restaurant, table=table)
    for line in lines:
        if not isinstance(line, dict):
            raise OrderError("Invalid item.")
        try:
            dish_id, qty = int(line.get("dish_id")), int(line.get("quantity"))
        except (TypeError, ValueError):
            raise OrderError("Invalid item.")
        if not 1 <= qty <= MAX_QTY:
            raise OrderError(f"Quantity must be between 1 and {MAX_QTY}.")
        dish = Dish.objects.filter(pk=dish_id, restaurant=table.restaurant, available=True).first()
        if dish is None:
            raise OrderError("A dish in your cart is no longer available.")
        price, label = dish.price, ""
        sizes = list(dish.sizes.all())
        if sizes:
            size = next((s for s in sizes if str(s.pk) == str(line.get("size_id"))), None)
            if size is None:
                raise OrderError(f"Choose a size for {dish.name}.")
            price, label = size.price, size.label
        note = str(line.get("note") or "")[:140]
        OrderItem.objects.create(order=order, dish=dish, size_label=label, unit_price=price, quantity=qty, note=note)
    notify(order.restaurant_id, table.pk)
    return order


@transaction.atomic
def advance_order(order, role):
    order = Order.objects.select_for_update().get(pk=order.pk)
    to = NEXT.get(order.status)
    if to is None:
        raise OrderError("This order is already served.")
    if role not in WHO[to]:
        raise PermissionDenied
    now = timezone.now()
    order.status = to
    if to == Order.COOKING:
        order.cooking_at = now
    elif to == Order.READY:
        order.ready_at = now
        order.items.filter(ready_at__isnull=True).update(ready_at=now)
    else:
        order.served_at = now
    order.save()
    notify(order.restaurant_id, order.table_id)
    return order


@transaction.atomic
def mark_station_ready(order, station, role):
    if role not in {StaffProfile.KITCHEN, StaffProfile.OWNER}:
        raise PermissionDenied
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.COOKING:
        raise OrderError("This order is not being cooked.")
    now = timezone.now()
    order.items.filter(dish__station=station, ready_at__isnull=True).update(ready_at=now)
    if not order.items.filter(ready_at__isnull=True).exists():
        order.status, order.ready_at = Order.READY, now
        order.save()
    notify(order.restaurant_id, order.table_id)
    return order


def call_waiter(table, kind):
    if kind not in dict(ServiceRequest.KINDS):
        raise OrderError("Unknown request.")
    # Tapping twice does not create a second request while one is still open.
    existing = ServiceRequest.objects.filter(table=table, kind=kind).exclude(status=ServiceRequest.DONE).first()
    if existing:
        return existing
    req = ServiceRequest.objects.create(restaurant=table.restaurant, table=table, kind=kind)
    notify(req.restaurant_id, table.pk)
    return req


@transaction.atomic
def update_request(req, action, role):
    if role not in {StaffProfile.WAITER, StaffProfile.OWNER}:
        raise PermissionDenied
    now = timezone.now()
    if action == "ack" and req.status == ServiceRequest.OPEN:
        req.status, req.acknowledged_at = ServiceRequest.ACKNOWLEDGED, now
    elif action == "done" and req.status != ServiceRequest.DONE:
        req.status, req.done_at = ServiceRequest.DONE, now
        req.acknowledged_at = req.acknowledged_at or now
    else:
        raise OrderError("That request cannot be changed this way.")
    req.save()
    notify(req.restaurant_id, req.table_id)
    return req
