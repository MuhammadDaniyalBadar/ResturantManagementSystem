"""All business rules live here: prices, bills, status changes, who may do what.
Views and consumers only call these and display the result."""

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (Bill, Deal, Dish, Guest, Member, Order, OrderItem, Payment, QuizAnswer, QuizQuestion, Review,
                     ServiceRequest, StaffProfile)

MAX_LINES, MAX_QTY, MAX_SPLIT, MAX_TIP = 30, 20, 10, 30
QUIZ_POINTS = 10

NEXT = {Order.PLACED: Order.COOKING, Order.COOKING: Order.READY, Order.READY: Order.SERVED}
WHO = {
    Order.COOKING: {StaffProfile.WAITER, StaffProfile.OWNER},
    Order.READY: {StaffProfile.KITCHEN, StaffProfile.OWNER},
    Order.SERVED: {StaffProfile.WAITER, StaffProfile.OWNER},
}
FRONT = {StaffProfile.WAITER, StaffProfile.OWNER}


class OrderError(ValueError):
    """A request the guest or staff member can fix; the message is safe to show."""


def notify(restaurant_id, table_id):
    """Tell staff, kitchen and the table's guests to refresh. Sent after the database commit."""
    layer = get_channel_layer()

    def send():
        for group in (f"rest-{restaurant_id}", f"table-{table_id}"):
            async_to_sync(layer.group_send)(group, {"type": "changed"})

    transaction.on_commit(send)


def notify_quiz(restaurant_id):
    layer = get_channel_layer()
    transaction.on_commit(lambda: async_to_sync(layer.group_send)(f"quiz-{restaurant_id}", {"type": "changed"}))


def whole(x, rounding=ROUND_HALF_UP):
    return Decimal(x).quantize(Decimal("1"), rounding=rounding)


# ---------- guests and members ----------

def join_guest(table, name):
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise OrderError("Please tell us your name.")
    if Guest.objects.filter(table=table, created_at__gte=timezone.now() - timedelta(hours=12)).count() >= 20:
        raise OrderError("Too many people have joined this table.")
    guest = Guest.objects.create(restaurant=table.restaurant, table=table, name=name)
    notify(table.restaurant_id, table.pk)  # everyone at the table sees who joined
    return guest


def join_member(restaurant, email):
    email = str(email or "").strip().lower()
    if "@" not in email or len(email) > 254 or " " in email:
        raise OrderError("That email does not look right.")
    return Member.objects.get_or_create(restaurant=restaurant, email=email)[0]


# ---------- bills ----------

def get_bill(table):
    """The table's open bill, created on first use. A paid bill is closed, so the next party starts a new one."""
    bill = Bill.objects.filter(table=table).exclude(status=Bill.PAID).first()
    return bill or Bill.objects.create(restaurant=table.restaurant, table=table)


def bill_summary(bill):
    items = {}
    for o in bill.orders.all():
        for i in o.items.all():
            key = (i.dish_id, i.size_label, i.unit_price)
            row = items.setdefault(key, {"name": i.dish.name, "size": i.size_label, "art": i.dish.picture, "qty": 0, "unit": i.unit_price})
            row["qty"] += i.quantity
    sub = sum((r["unit"] * r["qty"] for r in items.values()), Decimal(0))
    r = bill.restaurant
    service = whole(sub * r.service_percent / 100)
    tax = whole((sub + service) * r.tax_percent / 100)
    base = sub + service + tax
    split = max(1, bill.split)
    return {"items": list(items.values()), "sub": sub, "service": service, "tax": tax, "base": base, "split": split,
            "share": whole(base / split, ROUND_CEILING)}


def request_payment(bill, guest, method, tip, email, split, member=None):
    if method not in dict(Payment.METHODS):
        raise OrderError("Choose how you want to pay.")
    try:
        tip, split = int(tip or 0), (1 if split is None else int(split))
    except (TypeError, ValueError):
        raise OrderError("Invalid tip or split.")
    if not 0 <= tip <= MAX_TIP or not 1 <= split <= MAX_SPLIT:
        raise OrderError("Invalid tip or split.")
    email = str(email or "").strip().lower()
    if email and ("@" not in email or " " in email):
        raise OrderError("That email does not look right.")
    with transaction.atomic():
        bill = Bill.objects.select_for_update().get(pk=bill.pk)
        if bill.status == Bill.PAID:
            raise OrderError("This bill is already paid.")
        if not bill.orders.exists():
            raise OrderError("There is nothing to pay yet.")
        existing = list(bill.payments.all())
        if guest and any(p.guest_id == guest.pk for p in existing):
            raise OrderError("You already chose how to pay.")
        if existing:
            split = bill.split  # the first person's split holds for everyone
        if len(existing) >= split:
            raise OrderError("Every share of this bill is already taken.")
        bill.split, bill.status = split, Bill.PAYING
        bill.save()
        s = bill_summary(bill)
        tip_amount = whole(s["share"] * tip / 100)
        Payment.objects.create(bill=bill, guest=guest, member=member, method=method, email=email, share=s["share"], tip=tip_amount, amount=s["share"] + tip_amount)
        call_waiter(bill.table, ServiceRequest.BILL)
        notify(bill.restaurant_id, bill.table_id)
    return bill


def invoice_text(payment):
    bill, s = payment.bill, bill_summary(payment.bill)
    cur = bill.restaurant.currency
    lines = [f"{bill.restaurant.name} · Table {bill.table.number} · Bill #{bill.pk}", ""]
    lines += [f"{i['qty']} x {i['name']}{' (' + i['size'] + ')' if i['size'] else ''}  {cur} {i['unit'] * i['qty']:,.0f}" for i in s["items"]]
    lines += ["", f"Subtotal  {cur} {s['sub']:,.0f}", f"Service charge  {cur} {s['service']:,.0f}", f"Tax  {cur} {s['tax']:,.0f}", f"Bill total  {cur} {s['base']:,.0f}"]
    if s["split"] > 1:
        lines.append(f"Split {s['split']} ways, your share  {cur} {payment.share:,.0f}")
    if payment.tip:
        lines.append(f"Tip  {cur} {payment.tip:,.0f}")
    lines += [f"Paid by {payment.get_method_display()}  {cur} {payment.amount:,.0f}", "", "Thank you for dining with us."]
    return "\n".join(lines)


def close_bill(bill):
    """Mark the bill paid, free the table, credit rewards and email the invoices."""
    now = timezone.now()
    with transaction.atomic():
        bill = Bill.objects.select_for_update().get(pk=bill.pk)
        if bill.status == Bill.PAID:
            return bill
        bill.payments.filter(status=Payment.REQUESTED).update(status=Payment.PAID, paid_at=now)
        bill.status, bill.paid_at = Bill.PAID, now
        bill.save()
        ServiceRequest.objects.filter(table=bill.table, kind=ServiceRequest.BILL).exclude(status=ServiceRequest.DONE).update(status=ServiceRequest.DONE, done_at=now)
        for p in bill.payments.select_related("member", "bill__restaurant", "bill__table"):
            if p.member_id:
                Member.objects.filter(pk=p.member_id).update(points=p.member.points + int(p.share * bill.restaurant.reward_rate / 100))
            if p.email:
                send_mail(f"Your invoice from {bill.restaurant.name}", invoice_text(p), settings.DEFAULT_FROM_EMAIL, [p.email], fail_silently=True)
        notify(bill.restaurant_id, bill.table_id)
    return bill


def confirm_payment(payment, role):
    if role not in FRONT:
        raise PermissionDenied
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.PAID:
            raise OrderError("Already confirmed.")
        payment.status, payment.paid_at = Payment.PAID, timezone.now()
        payment.save()
        bill = payment.bill
        if bill.payments.filter(status=Payment.PAID).count() >= bill.split:
            close_bill(bill)
        else:
            notify(bill.restaurant_id, bill.table_id)
    return payment


def close_bill_by_staff(bill, role):
    if role not in FRONT:
        raise PermissionDenied
    return close_bill(bill)


def add_review(bill, guest, stars, comment):
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        raise OrderError("Pick a star rating.")
    if not 1 <= stars <= 5:
        raise OrderError("Pick a star rating.")
    if guest and Review.objects.filter(bill=bill, guest=guest).exists():
        raise OrderError("You already left a review.")
    return Review.objects.create(bill=bill, guest=guest, stars=stars, comment=str(comment or "")[:300])


# ---------- orders ----------

@transaction.atomic
def place_order(table, lines, guest=None):
    if not isinstance(lines, list) or not lines:
        raise OrderError("Your cart is empty.")
    if len(lines) > MAX_LINES:
        raise OrderError("Too many items in one order.")
    bill = get_bill(table)
    if bill.status != Bill.OPEN:
        raise OrderError("The bill is being paid. Ask your waiter to add more.")
    order = Order.objects.create(restaurant=table.restaurant, table=table, bill=bill, guest=guest)
    mains = {}
    for line in lines:
        if isinstance(line, dict) and not line.get("deal_id"):
            try:
                mains[int(line.get("dish_id"))] = mains.get(int(line.get("dish_id")), 0) + int(line.get("quantity"))
            except (TypeError, ValueError):
                pass
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
        price, label, deal = dish.price, "", None
        if line.get("deal_id"):
            deal = Deal.objects.filter(pk=line.get("deal_id"), restaurant=table.restaurant, active=True, partner=dish).first()
            if deal is None or mains.get(deal.dish_id, 0) < qty:
                raise OrderError(f"The deal on {dish.name} needs its main dish in the same order.")
            price = deal.price
        else:
            sizes = list(dish.sizes.all())
            if sizes:
                size = next((s for s in sizes if str(s.pk) == str(line.get("size_id"))), None)
                if size is None:
                    raise OrderError(f"Choose a size for {dish.name}.")
                price, label = size.price, size.label
        note = str(line.get("note") or "")[:140]
        OrderItem.objects.create(order=order, dish=dish, deal=deal, size_label=label, unit_price=price, quantity=qty, note=note)
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


# ---------- service requests ----------

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
def update_request(req, action, role, user=None):
    if role not in FRONT:
        raise PermissionDenied
    now = timezone.now()
    if action == "ack" and req.status == ServiceRequest.OPEN:
        req.status, req.acknowledged_at = ServiceRequest.ACKNOWLEDGED, now
        req.assigned = req.assigned or user
    elif action == "done" and req.status != ServiceRequest.DONE:
        req.status, req.done_at = ServiceRequest.DONE, now
        req.acknowledged_at = req.acknowledged_at or now
    else:
        raise OrderError("That request cannot be changed this way.")
    req.save()
    notify(req.restaurant_id, req.table_id)
    return req


@transaction.atomic
def assign_request(req, user, role):
    if role not in FRONT:
        raise PermissionDenied
    req.assigned = user
    req.save(update_fields=["assigned"])
    notify(req.restaurant_id, req.table_id)
    return req


# ---------- floor ----------

def table_status(table, bill, open_requests):
    """free, ordering, eating, bill or help, for the live floor map."""
    kinds = {r.kind for r in open_requests}
    if kinds - {ServiceRequest.BILL}:
        return "help"
    if bill is None:
        return "free"
    if bill.status == Bill.PAYING or ServiceRequest.BILL in kinds:
        return "bill"
    return "ordering" if any(o.status == Order.PLACED for o in bill.orders.all()) else "eating"


# ---------- quiz ----------

def next_question(guest):
    answered = guest.answers.values_list("question_id", flat=True)
    return QuizQuestion.objects.filter(restaurant=guest.restaurant).exclude(id__in=answered).first()


def answer_question(guest, question_id, choice):
    q = QuizQuestion.objects.filter(pk=question_id, restaurant=guest.restaurant).first()
    try:
        choice = int(choice)
    except (TypeError, ValueError):
        raise OrderError("Pick an answer.")
    if q is None or not 0 <= choice < len(q.options):
        raise OrderError("Pick an answer.")
    if QuizAnswer.objects.filter(guest=guest, question=q).exists():
        raise OrderError("You already answered that one.")
    QuizAnswer.objects.create(guest=guest, question=q, choice=choice, correct=(choice == q.answer))
    notify_quiz(guest.restaurant_id)
    return q.answer


def leaderboard(restaurant):
    since = timezone.now() - timedelta(hours=12)
    guests = (Guest.objects.filter(restaurant=restaurant, created_at__gte=since).select_related("table")
              .annotate(right=Count("answers", filter=Q(answers__correct=True))).filter(right__gt=0).order_by("-right", "name"))
    players = [{"id": g.id, "name": g.name, "table": g.table.number, "points": g.right * QUIZ_POINTS} for g in guests]
    tables = {}
    for p in players:
        tables[p["table"]] = tables.get(p["table"], 0) + p["points"]
    return {"players": players[:8], "tables": [{"table": t, "points": v} for t, v in sorted(tables.items(), key=lambda kv: -kv[1])][:8]}
