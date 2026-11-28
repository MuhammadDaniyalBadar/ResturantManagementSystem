"""All business rules live here: prices, bills, status changes, who may do what.
Views and consumers only call these and display the result."""

import re
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .models import (PLANS, TRIAL_DAYS, Bill, Deal, Dish, Guest, Ingredient, RecipeItem, Member, Order, OrderItem, Payment, PlanPayment, QuizAnswer,
                     QuizQuestion, Reservation, Restaurant, Review, ServiceRequest, StaffProfile, Table)

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
        for group in [f"rest-{restaurant_id}"] + ([f"table-{table_id}"] if table_id else []):
            async_to_sync(layer.group_send)(group, {"type": "changed"})

    transaction.on_commit(send)


def notify_restaurant(restaurant):
    """Every table's guests refresh too, e.g. when a dish sells out."""
    layer = get_channel_layer()
    groups = [f"rest-{restaurant.pk}"] + [f"table-{pk}" for pk in restaurant.tables.values_list("pk", flat=True)]
    transaction.on_commit(lambda: [async_to_sync(layer.group_send)(g, {"type": "changed"}) for g in groups])


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

def place_order(table, lines, guest=None, key=""):
    """key is a code the phone makes once per cart: sending the same cart twice (double tap, retry) gives back the first order."""
    key = str(key or "")[:40]
    if key:
        existing = Order.objects.filter(table=table, client_key=key).first()
        if existing:
            return existing
    try:
        return _place_order(table, lines, guest, key)
    except IntegrityError:
        if not key:
            raise
        return Order.objects.get(table=table, client_key=key)  # the other tap won the race


@transaction.atomic
def _place_order(table, lines, guest, key):
    if not isinstance(lines, list) or not lines:
        raise OrderError("Your cart is empty.")
    if len(lines) > MAX_LINES:
        raise OrderError("Too many items in one order.")
    if not table.restaurant.open_for_business:
        raise OrderError("Ordering from the table is paused at this restaurant. Please ask a waiter.")
    bill = get_bill(table)
    if bill.status != Bill.OPEN:
        raise OrderError("The bill is being paid. Ask your waiter to add more.")
    order = Order.objects.create(restaurant=table.restaurant, table=table, bill=bill, guest=guest, client_key=key)
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
        dish = Dish.objects.filter(pk=dish_id, restaurant=table.restaurant, available=True, retired=False).first()
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
        _move_stock(order.items.all(), -1)
        order.stock_taken = True
    elif to == Order.READY:
        order.ready_at = now
        order.items.filter(ready_at__isnull=True).update(ready_at=now)
    else:
        order.served_at = now
    order.save()
    notify(order.restaurant_id, order.table_id)
    return order


def _editable(order):
    """Orders can change only before they are served and while the bill is still open (no shares asked yet)."""
    if order.status in (Order.SERVED, Order.CANCELLED):
        raise OrderError("This order can no longer be changed.")
    if order.bill_id is None or order.bill.status != Bill.OPEN:
        raise OrderError("The bill is being paid, so this order can no longer be changed.")


@transaction.atomic
def cancel_order(order, by, guest=None):
    """Staff (by = their username) can cancel until served; a guest only their own order, before the kitchen has it.
    The order leaves the bill, so totals, the kitchen board and the floor map all drop it."""
    order = Order.objects.select_for_update(of=("self",)).select_related("bill").get(pk=order.pk)
    if guest is not None and (order.guest_id != guest.id or order.status != Order.PLACED):
        raise OrderError("The kitchen already has this order. Ask your waiter to change it.")
    _editable(order)
    table_id = order.table_id
    if order.stock_taken:
        _move_stock(order.items.all(), +1)
        order.stock_taken = False
    order.status, order.cancelled_at, order.cancelled_by, order.bill = Order.CANCELLED, timezone.now(), "guest" if guest else by, None
    order.save()
    notify(order.restaurant_id, table_id)
    return order


@transaction.atomic
def remove_item(item, by):
    """Staff remove one line from an order. Removing the last line cancels the order."""
    order = Order.objects.select_for_update(of=("self",)).select_related("bill").get(pk=item.order_id)
    _editable(order)
    if not item.deal_id:
        # a deal line needs as many of its main dish in the order
        left = sum(i.quantity for i in order.items.filter(dish_id=item.dish_id, deal__isnull=True).exclude(pk=item.pk))
        if (order.items.filter(deal__dish_id=item.dish_id).aggregate(n=Sum("quantity"))["n"] or 0) > left:
            raise OrderError("Remove the deal item that goes with this dish first.")
    if order.stock_taken:
        _move_stock([item], +1)
    item.delete()
    if not order.items.exists():
        return cancel_order(order, by)
    notify(order.restaurant_id, order.table_id)
    return order


def set_available(dish, available):
    dish.available, dish.auto_out = bool(available), False  # a person decided, so inventory stops managing it
    dish.save(update_fields=["available", "auto_out"])
    notify_restaurant(dish.restaurant)
    return dish


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
@transaction.atomic
def update_request(req, action, role, user=None):
    if role not in FRONT:
        raise PermissionDenied
    # re-read under a lock: an assignment made a moment ago must not be overwritten by this stale copy
    req = ServiceRequest.objects.select_for_update().get(pk=req.pk)
    now = timezone.now()
    if action == "ack" and req.status == ServiceRequest.OPEN:
        req.status, req.acknowledged_at = ServiceRequest.ACKNOWLEDGED, now
        req.assigned = req.assigned or user
    elif action == "done" and req.status != ServiceRequest.DONE:
        req.status, req.done_at = ServiceRequest.DONE, now
        req.acknowledged_at = req.acknowledged_at or now
    else:
        raise OrderError("That request cannot be changed this way.")
    req.save(update_fields=["status", "acknowledged_at", "done_at", "assigned"])
    notify(req.restaurant_id, req.table_id)
    return req


@transaction.atomic
@transaction.atomic
def assign_request(req, user, role):
    if role not in FRONT:
        raise PermissionDenied
    req = ServiceRequest.objects.select_for_update().get(pk=req.pk)
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


# ---------- team ----------

def set_access(user, role):
    """Owners use the admin for the menu and tables (scoped to their restaurant); other roles get no admin access."""
    user.is_staff = role == StaffProfile.OWNER
    user.save(update_fields=["is_staff"])
    if role == StaffProfile.OWNER:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="core"))
    else:
        user.user_permissions.clear()


def _check_password(password, user):
    try:
        validate_password(str(password or ""), user)
    except ValidationError as e:
        raise OrderError(" ".join(e.messages))


@transaction.atomic
def add_staff(restaurant, username, name, role, password):
    username = str(username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,30}", username):
        raise OrderError("Usernames are 3 to 30 letters, numbers, dots, dashes or underscores.")
    if role not in dict(StaffProfile.ROLES):
        raise OrderError("Pick a role.")
    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        raise OrderError("That username is taken.")
    check_limit(restaurant, "staff")
    user = User(username=username, first_name=" ".join(str(name or "").split())[:150])
    _check_password(password, user)
    user.set_password(password)
    user.save()
    StaffProfile.objects.create(user=user, restaurant=restaurant, role=role)
    set_access(user, role)
    return user


@transaction.atomic
def update_staff(actor, user, active=None, role=None, password=None):
    """Owner changes a teammate. Deactivating or changing a password ends that person's sessions."""
    if not hasattr(user, "profile") or user.profile.restaurant_id != actor.profile.restaurant_id:
        raise PermissionDenied
    if user.pk == actor.pk and (active is False or (role and role != StaffProfile.OWNER)):
        raise OrderError("You cannot switch off your own login or change your own role.")
    if role is not None:
        if role not in dict(StaffProfile.ROLES):
            raise OrderError("Pick a role.")
        user.profile.role = role
        user.profile.save(update_fields=["role"])
        set_access(user, role)
    if active and not user.is_active:
        check_limit(user.profile.restaurant, "staff")
    if active is not None:
        user.is_active = bool(active)
    if password:
        _check_password(password, user)
        user.set_password(password)
    user.save()
    return user


# ---------- plans and signup ----------

def check_limit(restaurant, kind):
    """kind is "tables" or "staff". Raises when the plan has no room for one more."""
    limit = restaurant.plan_info[kind]
    if limit is None:
        return
    used = restaurant.tables.count() if kind == "tables" else get_user_model().objects.filter(profile__restaurant=restaurant, is_active=True).count()
    if used >= limit:
        what = "tables" if kind == "tables" else "active staff logins"
        raise OrderError(f"The {restaurant.plan_info['name']} plan allows {limit} {what}. Upgrade the plan to add more.")


def unique_slug(name):
    from django.utils.text import slugify
    base = slugify(name)[:50] or "restaurant"
    slug, n = base, 2
    while Restaurant.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def add_tables(restaurant, count):
    """Adds tables laid out in rows of four on the floor map."""
    start = restaurant.tables.count()
    for i in range(start, start + count):
        check_limit(restaurant, "tables")
        row, col = divmod(i, 4)
        Table.objects.create(restaurant=restaurant, number=i + 1, x=14 + col * 24, y=min(90, 20 + row * 22), round=(i % 2 == 1))


@transaction.atomic
def create_restaurant(name, owner_name, username, password, tables, email=""):
    """Self-service signup: a restaurant on a free trial of the Pro plan, with its owner login and tables."""
    name = " ".join(str(name or "").split())[:120]
    if len(name) < 2:
        raise OrderError("Enter your restaurant's name.")
    try:
        tables = int(tables)
    except (TypeError, ValueError):
        raise OrderError("Enter how many tables you have.")
    plan = "pro"
    if not 1 <= tables <= PLANS[plan]["tables"]:
        raise OrderError(f"Start with 1 to {PLANS[plan]['tables']} tables. You can add more later.")
    restaurant = Restaurant.objects.create(name=name, slug=unique_slug(name), plan=plan, trial_ends=timezone.localdate() + timedelta(days=TRIAL_DAYS))
    user = add_staff(restaurant, username, owner_name, StaffProfile.OWNER, password)
    email = str(email or "").strip().lower()
    if email:
        if "@" not in email or " " in email:
            raise OrderError("That email does not look right.")
        user.email = email
        user.save(update_fields=["email"])
    add_tables(restaurant, tables)
    return restaurant, user


@transaction.atomic
def record_plan_payment(restaurant, plan, months, amount, reference="", by=""):
    """Extends the restaurant's paid period. Today the platform records bank or cash payments; a gateway would call this too."""
    if plan not in PLANS:
        raise OrderError("Pick a plan.")
    try:
        months, amount = int(months), Decimal(str(amount))
    except (TypeError, ValueError, ArithmeticError):
        raise OrderError("Enter the months and the amount.")
    if not 1 <= months <= 24 or amount < 0:
        raise OrderError("Months must be 1 to 24 and the amount cannot be negative.")
    restaurant = Restaurant.objects.select_for_update().get(pk=restaurant.pk)
    today = timezone.localdate()
    start = max(today, restaurant.paid_until or today)
    end = start + timedelta(days=30 * months)
    restaurant.plan, restaurant.paid_until, restaurant.suspended = plan, end, False
    restaurant.save(update_fields=["plan", "paid_until", "suspended"])
    PlanPayment.objects.create(restaurant=restaurant, plan=plan, months=months, amount=amount, reference=str(reference or "")[:120], recorded_by=str(by)[:150], period_end=end)
    notify_restaurant(restaurant)
    return restaurant


def set_plan(restaurant, plan):
    if plan not in PLANS:
        raise OrderError("Pick a plan.")
    restaurant.plan = plan
    restaurant.save(update_fields=["plan"])
    return restaurant


def set_suspended(restaurant, suspended):
    restaurant.suspended = bool(suspended)
    restaurant.save(update_fields=["suspended"])
    notify_restaurant(restaurant)
    return restaurant


def extend_trial(restaurant, days=TRIAL_DAYS):
    restaurant.trial_ends = max(timezone.localdate(), restaurant.trial_ends or timezone.localdate()) + timedelta(days=days)
    restaurant.save(update_fields=["trial_ends"])
    return restaurant


# ---------- reservations ----------

MAX_PARTY, BOOK_AHEAD_DAYS = 30, 60


def book_table(restaurant, name, phone, party, when, email="", note=""):
    """A guest asks for a table. Staff confirm it; the restaurant is told live and the guest by email if they gave one."""
    from datetime import datetime
    if not restaurant.open_for_business:
        raise OrderError("This restaurant is not taking bookings online right now. Please call them.")
    name, phone = " ".join(str(name or "").split())[:80], " ".join(str(phone or "").split())[:30]
    if len(name) < 2 or len(re.sub(r"\D", "", phone)) < 7:
        raise OrderError("Enter your name and a phone number the restaurant can call.")
    try:
        party = int(party)
        when = datetime.fromisoformat(str(when))
    except (TypeError, ValueError):
        raise OrderError("Pick a date, a time and how many people.")
    if not 1 <= party <= MAX_PARTY:
        raise OrderError(f"Online bookings are for 1 to {MAX_PARTY} people. Call the restaurant for bigger groups.")
    if timezone.is_naive(when):
        when = timezone.make_aware(when)
    now = timezone.now()
    if not now <= when <= now + timedelta(days=BOOK_AHEAD_DAYS):
        raise OrderError(f"Pick a time from now up to {BOOK_AHEAD_DAYS} days ahead.")
    email = str(email or "").strip().lower()
    if email and ("@" not in email or " " in email):
        raise OrderError("That email does not look right.")
    since = now - timedelta(hours=1)
    if Reservation.objects.filter(restaurant=restaurant, phone=phone, created_at__gte=since).count() >= 3:
        raise OrderError("You already sent a few bookings. The restaurant will call you.")
    res = Reservation.objects.create(restaurant=restaurant, name=name, phone=phone, email=email, party=party, when=when, note=str(note or "")[:300])
    notify(restaurant.pk, None)
    return res


RES_NEXT = {
    "confirm": ({Reservation.REQUESTED}, Reservation.CONFIRMED),
    "seat": ({Reservation.REQUESTED, Reservation.CONFIRMED}, Reservation.SEATED),
    "cancel": ({Reservation.REQUESTED, Reservation.CONFIRMED}, Reservation.CANCELLED),
    "no_show": ({Reservation.CONFIRMED, Reservation.REQUESTED}, Reservation.NO_SHOW),
}


def update_reservation(res, action, table=None):
    if action not in RES_NEXT:
        raise OrderError("Unknown action.")
    allowed, to = RES_NEXT[action]
    if res.status not in allowed:
        raise OrderError(f"This booking is already {res.get_status_display().lower()}.")
    if table is not None and table.restaurant_id != res.restaurant_id:
        raise PermissionDenied
    res.status = to
    if table is not None:
        res.table = table
    res.save(update_fields=["status", "table"])
    if action == "confirm" and res.email:
        r = res.restaurant
        send_mail(f"Your table at {r.name} is confirmed",
                  f"Hi {res.name},\n\nYour table for {res.party} at {r.name} on {timezone.localtime(res.when):%A %d %B at %H:%M} is confirmed.\n\nSee you soon.\n{r.name}",
                  settings.DEFAULT_FROM_EMAIL, [res.email], fail_silently=True)
    notify(res.restaurant_id, None)
    return res


# ---------- inventory ----------

def _move_stock(items, sign):
    """sign -1 takes ingredients for these order items, +1 puts them back. Then dishes that ran out are sold out."""
    used = {}
    for line in RecipeItem.objects.filter(dish__in=[i.dish_id for i in items]):
        for i in items:
            if i.dish_id == line.dish_id:
                used[line.ingredient_id] = used.get(line.ingredient_id, Decimal(0)) + line.amount * i.quantity
    for ing_id, amount in used.items():
        Ingredient.objects.filter(pk=ing_id).update(stock=F("stock") + sign * amount)
    if used:
        refresh_availability(Ingredient.objects.filter(pk__in=used).first().restaurant, used.keys())


def refresh_availability(restaurant, ingredient_ids):
    """A dish sells out when any ingredient cannot cover one more portion, and comes back when all can,
    unless a person marked it sold out by hand."""
    stock = dict(Ingredient.objects.filter(restaurant=restaurant).values_list("id", "stock"))
    changed = False
    for dish in Dish.objects.filter(restaurant=restaurant, recipe__ingredient_id__in=list(ingredient_ids)).distinct().prefetch_related("recipe"):
        enough = all(stock.get(line.ingredient_id, 0) >= line.amount for line in dish.recipe.all())
        if dish.available and not enough:
            dish.available, dish.auto_out, changed = False, True, True
        elif not dish.available and dish.auto_out and enough:
            dish.available, dish.auto_out, changed = True, False, True
        else:
            continue
        dish.save(update_fields=["available", "auto_out"])
    if changed:
        notify_restaurant(restaurant)


def _amount(value, what="amount"):
    try:
        value = Decimal(str(value)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError, TypeError):
        raise OrderError(f"Enter a number for the {what}.")
    if not value.is_finite() or abs(value) > Decimal("999999999"):
        raise OrderError(f"Enter a sensible {what}.")
    return value


@transaction.atomic
def adjust_stock(ingredient, add=None, count=None, low_at=None):
    """add = delivery (+) or waste (-); count = the number found in a stocktake; low_at = the warning level."""
    ing = Ingredient.objects.select_for_update().get(pk=ingredient.pk)
    if add is not None and add != "":
        ing.stock += _amount(add)
    if count is not None and count != "":
        ing.stock = _amount(count, "count")
    if low_at is not None and low_at != "":
        ing.low_at = max(Decimal(0), _amount(low_at, "warning level"))
    ing.save(update_fields=["stock", "low_at"])
    refresh_availability(ing.restaurant, [ing.pk])
    notify(ing.restaurant_id, None)
    return ing


def add_ingredient(restaurant, name, unit, stock, low_at):
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise OrderError("Name the ingredient.")
    if unit not in dict(Ingredient.UNITS):
        raise OrderError("Pick a unit.")
    if Ingredient.objects.filter(restaurant=restaurant, name__iexact=name).exists():
        raise OrderError("You already track that ingredient.")
    return Ingredient.objects.create(restaurant=restaurant, name=name, unit=unit, stock=_amount(stock or 0, "stock"), low_at=max(Decimal(0), _amount(low_at or 0, "warning level")))


def low_stock(restaurant):
    return list(Ingredient.objects.filter(restaurant=restaurant, stock__lte=F("low_at")).values("name", "stock", "unit"))
