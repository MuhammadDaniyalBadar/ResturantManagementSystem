import json
from datetime import timedelta
from decimal import Decimal
from functools import wraps

import segno
from django.conf import settings
from django import forms
from django.contrib.auth import get_user_model, login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, F, Max, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import (PLANS, bundled_photo, TRIAL_DAYS, Bill, Category, Deal, Dish, Guest, Ingredient, Member, Order, OrderItem, Payment, PlanPayment, QuizQuestion,
                     Reservation, Restaurant, Review, ServiceRequest, StaffProfile, Table)

OWNER, WAITER, KITCHEN = StaffProfile.OWNER, StaffProfile.WAITER, StaffProfile.KITCHEN
User = get_user_model()


# ---------- helpers ----------

def staff_only(*roles, api=False):
    """Login and a staff profile (optionally one of `roles`) are required. Sets request.profile."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if api:
                    return JsonResponse({"error": "Please log in."}, status=403)
                return redirect(f"{reverse('login')}?next={request.path}")
            profile = getattr(request.user, "profile", None)
            if profile is None or (roles and profile.role not in roles):
                if api:
                    return JsonResponse({"error": "Not allowed."}, status=403)
                raise PermissionDenied
            request.profile = profile
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def json_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except services.OrderError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except PermissionDenied:
            return JsonResponse({"error": "Not allowed."}, status=403)

    return wrapper


def read_json(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise services.OrderError("Bad request.")
    if not isinstance(data, dict):
        raise services.OrderError("Bad request.")
    return data


def seconds_since(moment, now):
    return max(0, int((now - moment).total_seconds())) if moment else None


def order_json(order, now):
    items = list(order.items.all())
    left = None
    estimate = max((i.dish.prep_minutes for i in items), default=0) * 60
    if order.status == Order.COOKING and order.cooking_at:
        left = max(0, estimate - seconds_since(order.cooking_at, now))
    return {
        "id": order.id,
        "table": order.table.number,
        "guest": order.guest.name if order.guest_id else "",
        "guest_id": order.guest_id,
        "status": order.status,
        "label": order.get_status_display(),
        "seconds_left": left,
        "estimate": estimate,
        "seconds_old": seconds_since(order.created_at, now),
        "total": str(order.total),
        "items": [
            {"id": i.id, "name": i.dish.name, "size": i.size_label, "qty": i.quantity, "note": i.note, "station": i.dish.station, "ready": bool(i.ready_at), "art": i.dish.picture, "prep": i.dish.prep_minutes, "deal": bool(i.deal_id)}
            for i in items
        ],
    }


def request_json(req, now):
    return {
        "id": req.id,
        "table": req.table.number,
        "kind": req.kind,
        "label": req.get_kind_display(),
        "status": req.status,
        "status_label": req.get_status_display(),
        "seconds_old": seconds_since(req.created_at, now),
        "assigned": (req.assigned.first_name or req.assigned.username) if req.assigned_id else "",
        "assigned_id": req.assigned_id,
        "response": seconds_since(req.created_at, req.acknowledged_at) if req.acknowledged_at else None,
    }


def orders_qs(**filters):
    return Order.objects.filter(**filters).select_related("table", "guest").prefetch_related("items__dish")


def payment_json(p):
    return {"id": p.id, "table": p.bill.table.number, "guest": p.guest.name if p.guest_id else "Guest", "method": p.method, "method_label": p.get_method_display(),
            "share": str(p.share), "tip": str(p.tip), "amount": str(p.amount), "status": p.status, "email": p.email}


def money(d):
    return str(Decimal(d))


# ---------- home ----------

def home(request):
    if request.user.is_authenticated and request.user.is_superuser and not hasattr(request.user, "profile"):
        return redirect("platform")
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
    if profile:
        return redirect("kitchen" if profile.role == KITCHEN else "staff")
    return render(request, "home.html")


# ---------- guest ----------

def get_table(code):
    return get_object_or_404(Table.objects.select_related("restaurant"), code=code)


def party_guests(table, now):
    """Names of the people at the table now: joined in the last 6 hours and after the table's last bill was closed."""
    since = now - timedelta(hours=6)
    last = Bill.objects.filter(table=table, status=Bill.PAID, paid_at__isnull=False).order_by("-paid_at").values_list("paid_at", flat=True).first()
    return [g.name for g in Guest.objects.filter(table=table, created_at__gte=max(since, last) if last else since)]


def session_guest(request, table):
    gid = request.session.get("guests", {}).get(table.code)
    return Guest.objects.filter(pk=gid, table=table).first() if gid else None


def session_member(request, restaurant):
    mid = request.session.get("members", {}).get(str(restaurant.pk))
    return Member.objects.filter(pk=mid, restaurant=restaurant).first() if mid else None


def menu_data(restaurant):
    deals = {}
    for d in Deal.objects.filter(restaurant=restaurant, active=True).select_related("partner"):
        deals.setdefault(d.dish_id, []).append({"id": d.id, "partner_id": d.partner_id, "partner": d.partner.name, "art": d.partner.picture, "price": str(d.price), "normal": str(d.partner.price)})
    categories = []
    for category in Category.objects.filter(restaurant=restaurant).prefetch_related("dish_set__sizes"):
        dishes = [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "price": str(d.price),
                "allergens": [a.strip() for a in d.allergens.split(",") if a.strip()],
                "spice": d.get_spice_display() if d.spice else "",
                "sizes": [{"id": s.id, "label": s.label, "price": str(s.price)} for s in d.sizes.all()],
                "art": d.picture,
                "featured": d.featured,
                "available": d.available,
                "diet": d.diet,
                "serves": d.serves,
                "badge": d.get_badge_display() if d.badge else "",
                "deals": deals.get(d.id, []),
            }
            for d in category.dish_set.all()
            if not d.retired
        ]
        if dishes:
            categories.append({"name": category.name, "dishes": dishes})
    return {"currency": restaurant.currency, "categories": categories}


@require_GET
def table_page(request, code):
    table = get_table(code)
    r = table.restaurant
    return render(request, "guest.html", {"table": table, "restaurant": r, "menu": menu_data(r), "logo_url": r.logo.url if r.logo else ""})


@require_GET
def table_state(request, code):
    table = get_table(code)
    r, now = table.restaurant, timezone.now()
    guest, member = session_guest(request, table), session_member(request, r)
    bill = Bill.objects.filter(table=table).exclude(status=Bill.PAID).first()
    orders = orders_qs(bill=bill) if bill else Order.objects.none()
    requests = ServiceRequest.objects.filter(table=table).exclude(status=ServiceRequest.DONE).select_related("table", "assigned")
    out = {
        "sold_out": list(Dish.objects.filter(restaurant=r, available=False).values_list("id", flat=True)),
        "guest": {"id": guest.id, "name": guest.name} if guest else None,
        "guests": party_guests(table, now),
        "orders": [order_json(o, now) for o in orders],
        "requests": [request_json(q, now) for q in requests],
        "wifi": {"name": r.wifi_name, "password": r.wifi_password} if r.wifi_name else None,
        "reward": {"goal": r.reward_goal, "text": r.reward_text, "rate": r.reward_rate, "points": member.points if member else 0, "email": member.email if member else ""},
        "bill": None, "payments": [], "paid": None,
    }
    if bill:
        s = services.bill_summary(bill)
        out["bill"] = {"id": bill.id, "status": bill.status, "split": s["split"], "sub": money(s["sub"]), "service": money(s["service"]), "tax": money(s["tax"]), "base": money(s["base"]), "share": money(s["share"]),
                       "service_percent": format(r.service_percent.normalize(), "f"), "tax_percent": format(r.tax_percent.normalize(), "f"),
                       "items": [{"name": i["name"], "size": i["size"], "art": i["art"], "qty": i["qty"], "total": money(i["unit"] * i["qty"])} for i in s["items"]],
                       "earn": int(s["base"] * r.reward_rate / 100)}
        out["payments"] = [payment_json(p) | {"mine": bool(guest and p.guest_id == guest.id)} for p in bill.payments.select_related("guest", "bill__table")]
    elif guest:
        paid = Bill.objects.filter(table=table, status=Bill.PAID, payments__guest=guest).order_by("-paid_at").first()
        if paid and paid.paid_at > now - timedelta(hours=3):
            mine = paid.payments.filter(guest=guest).first()
            out["paid"] = {"id": paid.id, "amount": money(mine.amount), "method": mine.get_method_display(), "email": mine.email, "reviewed": paid.reviews.filter(guest=guest).exists()}
    return JsonResponse(out)


@require_POST
@json_api
def table_join(request, code):
    table = get_table(code)
    guest = services.join_guest(table, read_json(request).get("name"))
    request.session.setdefault("guests", {})[table.code] = guest.id
    request.session.modified = True
    return JsonResponse({"ok": True, "guest": {"id": guest.id, "name": guest.name}})


@require_POST
@json_api
def table_member(request, code):
    table = get_table(code)
    member = services.join_member(table.restaurant, read_json(request).get("email"))
    request.session.setdefault("members", {})[str(table.restaurant_id)] = member.id
    request.session.modified = True
    return JsonResponse({"ok": True, "points": member.points})


@require_POST
@json_api
def table_order(request, code):
    table = get_table(code)
    order = services.place_order(table, read_json(request).get("items"), session_guest(request, table))
    return JsonResponse({"ok": True, "order_id": order.id})


@require_POST
@json_api
def table_cancel(request, code, pk):
    table = get_table(code)
    guest = session_guest(request, table)
    order = get_object_or_404(Order, pk=pk, table=table)
    if guest is None:
        raise services.OrderError("Tell us your name first.")
    services.cancel_order(order, "guest", guest=guest)
    return JsonResponse({"ok": True})


@require_POST
@json_api
def table_call(request, code):
    table = get_table(code)
    req = services.call_waiter(table, read_json(request).get("kind"))
    return JsonResponse({"ok": True, "request_id": req.id})


@require_POST
@json_api
def table_pay(request, code):
    table = get_table(code)
    data = read_json(request)
    bill = Bill.objects.filter(table=table).exclude(status=Bill.PAID).first()
    if bill is None:
        raise services.OrderError("There is nothing to pay yet.")
    services.request_payment(bill, session_guest(request, table), data.get("method"), data.get("tip"), data.get("email"), data.get("split"), session_member(request, table.restaurant))
    return JsonResponse({"ok": True})


@require_POST
@json_api
def table_review(request, code):
    table = get_table(code)
    guest = session_guest(request, table)
    bill = Bill.objects.filter(table=table, payments__guest=guest).order_by("-created_at").first() if guest else None
    if bill is None:
        raise services.OrderError("Pay your share first, then tell us how it was.")
    data = read_json(request)
    services.add_review(bill, guest, data.get("stars"), data.get("comment"))
    return JsonResponse({"ok": True})


@require_GET
@json_api
def table_quiz(request, code):
    table = get_table(code)
    guest = session_guest(request, table)
    q = services.next_question(guest) if guest else None
    mine = guest.answers.filter(correct=True).count() * services.QUIZ_POINTS if guest else 0
    return JsonResponse({"question": {"id": q.id, "text": q.text, "options": q.options} if q else None, "me": mine, "board": services.leaderboard(table.restaurant), "joined": bool(guest)})


@require_POST
@json_api
def table_quiz_answer(request, code):
    table = get_table(code)
    guest = session_guest(request, table)
    if guest is None:
        raise services.OrderError("Tell us your name first.")
    data = read_json(request)
    right = services.answer_question(guest, data.get("question_id"), data.get("choice"))
    return JsonResponse({"ok": True, "right": right, "correct": int(data.get("choice")) == right})


# ---------- staff ----------

@staff_only(WAITER, OWNER)
def staff_page(request):
    return render(request, "staff.html", {"restaurant": request.profile.restaurant, "role": request.profile.role})


def floor_json(restaurant, now):
    open_bills = {b.table_id: b for b in Bill.objects.filter(restaurant=restaurant).exclude(status=Bill.PAID).prefetch_related("orders__items__dish", "payments")}
    reqs = {}
    for q in ServiceRequest.objects.filter(restaurant=restaurant).exclude(status=ServiceRequest.DONE).select_related("assigned"):
        reqs.setdefault(q.table_id, []).append(q)
    out = []
    for t in Table.objects.filter(restaurant=restaurant):
        bill, rq = open_bills.get(t.id), reqs.get(t.id, [])
        info = None
        if bill:
            s = services.bill_summary(bill)
            info = {"id": bill.id, "status": bill.status, "total": money(s["base"]), "items": [f"{i['qty']} × {i['name']}" for i in s["items"]], "payments": [payment_json(p) | {"mine": False} for p in bill.payments.all()]}
        out.append({
            "n": t.number, "x": t.x, "y": t.y, "round": t.round, "status": services.table_status(t, bill, rq), "bill": info,
            "guests": party_guests(t, now),
            "requests": [{"id": q.id, "label": q.get_kind_display(), "status": q.status, "assigned": (q.assigned.first_name or q.assigned.username) if q.assigned_id else "", "seconds_old": seconds_since(q.created_at, now)} for q in rq],
        })
    return out


@require_GET
@staff_only(api=True)
def staff_data(request):
    restaurant, role, now = request.profile.restaurant, request.profile.role, timezone.now()
    orders = []
    for o in orders_qs(restaurant=restaurant).exclude(status=Order.SERVED).filter(bill__isnull=False).exclude(bill__status=Bill.PAID):
        data = order_json(o, now)
        nxt = services.NEXT.get(o.status)
        data["action"] = {"cooking": "Send to kitchen", "served": "Mark served"}.get(nxt) if role in services.WHO.get(nxt, ()) else None
        orders.append(data)
    reqs = ServiceRequest.objects.filter(restaurant=restaurant).exclude(status=ServiceRequest.DONE).select_related("table", "assigned")
    pay = Payment.objects.filter(bill__restaurant=restaurant, status=Payment.REQUESTED).select_related("guest", "bill__table")
    team = [{"id": u.id, "name": u.first_name or u.username} for u in User.objects.filter(profile__restaurant=restaurant, profile__role__in=[WAITER, OWNER])]
    menu = [{"id": d.id, "name": d.name, "category": d.category.name, "art": d.picture, "available": d.available}
            for d in Dish.objects.filter(restaurant=restaurant, retired=False).select_related("category")]
    return JsonResponse({"currency": restaurant.currency, "orders": orders, "menu": menu, "role": role, "requests": [request_json(r, now) for r in reqs], "tables": floor_json(restaurant, now), "payments": [payment_json(p) for p in pay], "team": team, "me": request.user.id})


@require_POST
@staff_only(WAITER, OWNER, api=True)
@json_api
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk, restaurant=request.profile.restaurant)
    services.cancel_order(order, request.user.username)
    return JsonResponse({"ok": True})


@require_POST
@staff_only(WAITER, OWNER, api=True)
@json_api
def item_remove(request, pk):
    item = get_object_or_404(OrderItem, pk=pk, order__restaurant=request.profile.restaurant)
    services.remove_item(item, request.user.username)
    return JsonResponse({"ok": True})


@require_POST
@staff_only(api=True)
@json_api
def dish_available(request, pk):
    dish = get_object_or_404(Dish, pk=pk, restaurant=request.profile.restaurant)
    services.set_available(dish, read_json(request).get("available"))
    return JsonResponse({"ok": True})


@require_POST
@staff_only(api=True)
@json_api
def order_advance(request, pk):
    order = get_object_or_404(Order, pk=pk, restaurant=request.profile.restaurant)
    services.advance_order(order, request.profile.role)
    return JsonResponse({"ok": True})


@require_POST
@staff_only(api=True)
@json_api
def request_action(request, pk, action):
    req = get_object_or_404(ServiceRequest, pk=pk, restaurant=request.profile.restaurant)
    if action == "assign":
        user = get_object_or_404(User, pk=read_json(request).get("user_id"), profile__restaurant=request.profile.restaurant)
        services.assign_request(req, user, request.profile.role)
    else:
        services.update_request(req, action, request.profile.role, request.user)
    return JsonResponse({"ok": True})


@require_POST
@staff_only(api=True)
@json_api
def payment_paid(request, pk):
    payment = get_object_or_404(Payment, pk=pk, bill__restaurant=request.profile.restaurant)
    services.confirm_payment(payment, request.profile.role)
    return JsonResponse({"ok": True})


@require_POST
@staff_only(api=True)
@json_api
def bill_close(request, pk):
    bill = get_object_or_404(Bill, pk=pk, restaurant=request.profile.restaurant)
    services.close_bill_by_staff(bill, request.profile.role)
    return JsonResponse({"ok": True})


# ---------- kitchen ----------

@staff_only(KITCHEN, OWNER)
def kitchen_page(request):
    return render(request, "kitchen.html", {"restaurant": request.profile.restaurant})


@require_GET
@staff_only(KITCHEN, OWNER, api=True)
def kitchen_data(request):
    now = timezone.now()
    # every station shows as a column, even with nothing cooking
    stations = {s: [] for s in Dish.objects.filter(restaurant=request.profile.restaurant, retired=False).values_list("station", flat=True).distinct()}
    for o in orders_qs(restaurant=request.profile.restaurant, status=Order.COOKING).order_by("cooking_at"):
        data = order_json(o, now)
        for station in sorted({i["station"] for i in data["items"]}):
            lines = [i for i in data["items"] if i["station"] == station]
            if all(i["ready"] for i in lines):
                continue
            target = max(i["prep"] for i in lines) * 60  # over this and the ticket turns red
            ticket = {"order_id": o.id, "table": o.table.number, "elapsed": seconds_since(o.cooking_at, now), "target": target, "items": lines}
            stations.setdefault(station, []).append(ticket)
    waiting = Order.objects.filter(restaurant=request.profile.restaurant, status=Order.PLACED).count()
    return JsonResponse({"waiting": waiting, "low": services.low_stock(request.profile.restaurant), "stations": [{"name": n, "tickets": t} for n, t in sorted(stations.items())]})


@require_POST
@staff_only(KITCHEN, OWNER, api=True)
@json_api
def kitchen_ready(request, pk):
    order = get_object_or_404(Order, pk=pk, restaurant=request.profile.restaurant)
    services.mark_station_ready(order, str(read_json(request).get("station", "")), request.profile.role)
    return JsonResponse({"ok": True})


# ---------- owner ----------

@staff_only(OWNER)
def qr_sheet(request):
    tables = [
        {"number": t.number, "url": url, "svg": segno.make(url, error="m").svg_inline(scale=5, border=2, light="#ffffff", dark="#120d0b")}
        for t in Table.objects.filter(restaurant=request.profile.restaurant)
        for url in [request.build_absolute_uri(reverse("table", args=[t.code]))]
    ]
    return render(request, "qr.html", {"restaurant": request.profile.restaurant, "tables": tables})


@staff_only(OWNER)
def owner_page(request):
    return render(request, "owner.html", {"restaurant": request.profile.restaurant})


@require_GET
@staff_only(OWNER, api=True)
def owner_data(request):
    r, now = request.profile.restaurant, timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=6)
    paid = Bill.objects.filter(restaurant=r, status=Bill.PAID)
    day_sales = {}
    for b in paid.filter(paid_at__gte=week).prefetch_related("orders__items"):
        key = timezone.localtime(b.paid_at).date()
        day_sales[key] = day_sales.get(key, Decimal(0)) + services.bill_summary(b)["base"]
    days = [(week + timedelta(days=i)).date() for i in range(7)]
    live = Order.objects.filter(restaurant=r).exclude(status=Order.CANCELLED)
    prep = live.filter(ready_at__isnull=False, cooking_at__isnull=False, created_at__gte=today)
    prep_secs = [(o.ready_at - o.cooking_at).total_seconds() for o in prep]
    best = (OrderItem.objects.filter(order__restaurant=r, order__created_at__gte=week).exclude(order__status=Order.CANCELLED).values(name=F("dish__name"), art=F("dish__art"), photo=F("dish__photo")).annotate(qty=Sum("quantity")).order_by("-qty")[:5])
    hours = dict(live.filter(created_at__gte=week).annotate(h=ExtractHour("created_at")).values_list("h").annotate(c=Count("id")).order_by("h"))
    reviews = Review.objects.filter(bill__restaurant=r).select_related("guest", "bill__table")
    avg = reviews.aggregate(a=Avg("stars"))["a"]
    staff_n = User.objects.filter(profile__restaurant=r).count()
    setup = [
        {"label": "Add your dishes", "done": Dish.objects.filter(restaurant=r).exists(), "url": "/admin/core/dish/add/"},
        {"label": "Set your brand, tax and WiFi", "done": bool(r.wifi_name or r.logo or r.tagline), "url": reverse("settings")},
        {"label": "Print the table QR codes", "done": Order.objects.filter(restaurant=r).exists(), "url": reverse("qr")},
        {"label": "Add your waiters and kitchen", "done": staff_n > 1, "url": reverse("team")},
    ]
    return JsonResponse({
        "currency": r.currency,
        "setup": setup if not all(s["done"] for s in setup) else [],
        "low": services.low_stock(r),
        "tables_total": r.tables.count(), "tables_in_use": Bill.objects.filter(restaurant=r).exclude(status=Bill.PAID).count(),
        "orders_today": live.filter(created_at__gte=today).count(),
        "sales_today": money(day_sales.get(today.date(), Decimal(0))),
        "avg_prep": int(sum(prep_secs) / len(prep_secs)) if prep_secs else None,
        "days": [{"label": d.strftime("%a"), "sales": float(day_sales.get(d, 0))} for d in days],
        "hours": [{"hour": h, "orders": hours.get(h, 0)} for h in range(10, 24)],
        "best": [{"name": b["name"], "art": settings.MEDIA_URL + b["photo"] if b["photo"] else (bundled_photo(b["name"]) or b["art"]), "qty": b["qty"]} for b in best],
        "rating": round(avg, 1) if avg else None, "review_count": reviews.count(),
        "reviews": [{"stars": v.stars, "comment": v.comment, "guest": v.guest.name if v.guest_id else "Guest", "table": v.bill.table.number, "age": seconds_since(v.created_at, now)} for v in reviews[:12]],
        "tables": floor_json(r, now),
    })


# ---------- team (owner) ----------

def team_json(restaurant, me):
    return [{"id": u.id, "username": u.username, "name": u.first_name, "role": u.profile.role, "active": u.is_active, "me": u.id == me.id,
             "last_login": u.last_login.isoformat() if u.last_login else None}
            for u in User.objects.filter(profile__restaurant=restaurant).select_related("profile").order_by("profile__role", "username")]


@staff_only(OWNER)
def team_page(request):
    return render(request, "team.html", {"restaurant": request.profile.restaurant})


@staff_only(OWNER, api=True)
@json_api
def team_data(request):
    r = request.profile.restaurant
    if request.method == "POST":
        d = read_json(request)
        services.add_staff(r, d.get("username"), d.get("name"), d.get("role"), d.get("password"))
    return JsonResponse({"team": team_json(r, request.user), "roles": StaffProfile.ROLES})


@require_POST
@staff_only(OWNER, api=True)
@json_api
def team_member(request, pk):
    user = get_object_or_404(User, pk=pk, profile__restaurant=request.profile.restaurant)
    d = read_json(request)
    services.update_staff(request.user, user, active=d.get("active"), role=d.get("role"), password=d.get("password"))
    return JsonResponse({"ok": True})


# ---------- inventory (owner) ----------

@staff_only(OWNER)
def inventory_page(request):
    return render(request, "inventory.html", {"restaurant": request.profile.restaurant, "units": Ingredient.UNITS})


@staff_only(OWNER, api=True)
@json_api
def inventory_data(request):
    r = request.profile.restaurant
    if request.method == "POST":
        d = read_json(request)
        services.add_ingredient(r, d.get("name"), d.get("unit"), d.get("stock"), d.get("low_at"))
    rows = []
    for i in Ingredient.objects.filter(restaurant=r).prefetch_related("uses__dish"):
        rows.append({"id": i.id, "name": i.name, "unit": i.unit, "stock": money(i.stock), "low_at": money(i.low_at), "low": i.stock <= i.low_at,
                     "dishes": [{"name": u.dish.name, "amount": money(u.amount), "portions": int(i.stock // u.amount) if u.amount > 0 and i.stock > 0 else 0, "available": u.dish.available}
                                for u in i.uses.all()]})
    return JsonResponse({"ingredients": rows, "auto_out": list(Dish.objects.filter(restaurant=r, auto_out=True).values_list("name", flat=True))})


@require_POST
@staff_only(OWNER, api=True)
@json_api
def inventory_adjust(request, pk):
    ing = get_object_or_404(Ingredient, pk=pk, restaurant=request.profile.restaurant)
    d = read_json(request)
    services.adjust_stock(ing, add=d.get("add"), count=d.get("count"), low_at=d.get("low_at"))
    return JsonResponse({"ok": True})


# ---------- signup, settings and plan ----------

SIGNUPS_PER_HOUR = 5


def signup(request):
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        return redirect("owner")
    error, values = "", {}
    if request.method == "POST":
        values = {k: request.POST.get(k, "") for k in ("restaurant", "name", "username", "email", "tables")}
        key = "signup:" + request.META.get("REMOTE_ADDR", "")
        if request.POST.get("website"):  # hidden field only bots fill in
            error = "Something went wrong. Please try again."
        elif cache.get(key, 0) >= SIGNUPS_PER_HOUR:
            error = "Too many sign-ups from this connection. Try again in an hour."
        else:
            try:
                restaurant, user = services.create_restaurant(values["restaurant"], values["name"], values["username"], request.POST.get("password"), values["tables"], values["email"])
            except services.OrderError as e:
                error = str(e)
            else:
                cache.set(key, cache.get(key, 0) + 1, 3600)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect("owner")
    return render(request, "signup.html", {"error": error, "v": values, "plans": PLANS, "trial_days": TRIAL_DAYS}, status=400 if error else 200)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Restaurant
        fields = ["name", "tagline", "brand_color", "logo", "currency", "service_percent", "tax_percent", "wifi_name", "wifi_password", "reward_rate", "reward_goal", "reward_text"]
        widgets = {"brand_color": forms.TextInput(attrs={"type": "color"})}


@staff_only(OWNER)
def settings_page(request):
    r = request.profile.restaurant
    form = SettingsForm(request.POST or None, request.FILES or None, instance=r)
    saved = False
    if request.method == "POST" and form.is_valid():
        form.save()
        services.notify_restaurant(r)
        saved = True
    return render(request, "settings.html", {"restaurant": r, "form": form, "saved": saved, "plans": PLANS,
                                             "payments": r.plan_payments.all()[:12], "tables_used": r.tables.count(),
                                             "staff_used": User.objects.filter(profile__restaurant=r, is_active=True).count(),
                                             "book_url": request.build_absolute_uri(reverse("book", args=[r.slug])) if r.slug else ""})


# ---------- reservations ----------

def book(request, slug):
    r = get_object_or_404(Restaurant, slug=slug)
    error, v, done = "", {}, None
    if request.method == "POST":
        v = {k: request.POST.get(k, "") for k in ("name", "phone", "email", "party", "date", "time", "note")}
        if request.POST.get("website"):
            error = "Something went wrong. Please try again."
        else:
            try:
                done = services.book_table(r, v["name"], v["phone"], v["party"], f"{v['date']}T{v['time']}", v["email"], v["note"])
            except services.OrderError as e:
                error = str(e)
    today = timezone.localdate()
    return render(request, "book.html", {"restaurant": r, "error": error, "v": v, "done": done, "today": today.isoformat(),
                                         "last": (today + timedelta(days=services.BOOK_AHEAD_DAYS)).isoformat(),
                                         "logo_url": r.logo.url if r.logo else ""}, status=400 if error else 200)


@staff_only(WAITER, OWNER)
def reservations_page(request):
    return render(request, "reservations.html", {"restaurant": request.profile.restaurant})


def reservation_json(x):
    return {"id": x.id, "name": x.name, "phone": x.phone, "email": x.email, "party": x.party, "when": timezone.localtime(x.when).isoformat(),
            "note": x.note, "status": x.status, "status_label": x.get_status_display(), "table": x.table.number if x.table_id else None}


@require_GET
@staff_only(WAITER, OWNER, api=True)
def reservations_data(request):
    r = request.profile.restaurant
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = Reservation.objects.filter(restaurant=r, when__gte=start).select_related("table")[:300]
    return JsonResponse({"reservations": [reservation_json(x) for x in rows], "tables": list(r.tables.values_list("id", "number")),
                         "book_url": request.build_absolute_uri(reverse("book", args=[r.slug])) if r.slug else ""})


@require_POST
@staff_only(WAITER, OWNER, api=True)
@json_api
def reservation_action(request, pk, action):
    res = get_object_or_404(Reservation, pk=pk, restaurant=request.profile.restaurant)
    tid = read_json(request).get("table_id")
    table = get_object_or_404(Table, pk=tid, restaurant=request.profile.restaurant) if tid else None
    services.update_reservation(res, action, table)
    return JsonResponse({"ok": True})


# ---------- platform (you, running the service) ----------

def platform_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


@platform_only
def platform_page(request):
    return render(request, "platform.html", {"plans": PLANS})


@platform_only
def platform_data(request):
    now, since = timezone.now(), timezone.now() - timedelta(days=30)
    sales = dict(OrderItem.objects.filter(order__bill__status=Bill.PAID, order__bill__paid_at__gte=since).values_list("order__restaurant")
                 .annotate(t=Sum(F("unit_price") * F("quantity"))).order_by())
    orders = dict(Order.objects.filter(created_at__gte=since).exclude(status=Order.CANCELLED).values_list("restaurant").annotate(c=Count("id")).order_by())
    last = dict(Order.objects.values_list("restaurant").annotate(m=Max("created_at")).order_by())
    staff = dict(User.objects.filter(is_active=True, profile__isnull=False).values_list("profile__restaurant").annotate(c=Count("id")).order_by())
    owners = {}
    for u in User.objects.filter(profile__role=OWNER).select_related("profile").order_by("date_joined"):
        owners.setdefault(u.profile.restaurant_id, u)
    rows = []
    for r in Restaurant.objects.annotate(n_tables=Count("tables")).order_by("-created_at", "-id"):
        o = owners.get(r.id)
        rows.append({"id": r.id, "name": r.name, "slug": r.slug, "status": r.status, "plan": r.plan, "plan_name": r.plan_info["name"], "price": r.plan_info["price"],
                     "trial_ends": r.trial_ends.isoformat() if r.trial_ends else None, "paid_until": r.paid_until.isoformat() if r.paid_until else None,
                     "days_left": r.days_left, "tables": r.n_tables, "staff": staff.get(r.id, 0), "orders_30": orders.get(r.id, 0),
                     "sales_30": money(sales.get(r.id) or 0), "currency": r.currency,
                     "last_order": seconds_since(last[r.id], now) if r.id in last else None,
                     "owner": {"name": o.first_name or o.username, "username": o.username, "email": o.email} if o else None,
                     "created": r.created_at.date().isoformat() if r.created_at else None})
    month = timezone.localdate().replace(day=1)
    pays = PlanPayment.objects.select_related("restaurant")
    return JsonResponse({
        "restaurants": rows,
        "counts": {s: sum(1 for x in rows if x["status"] == s) for s in ("active", "trial", "expired", "suspended")},
        "mrr": sum(x["price"] for x in rows if x["status"] == "active"),
        "collected_month": money(pays.filter(paid_at__date__gte=month).aggregate(t=Sum("amount"))["t"] or 0),
        "payments": [{"restaurant": p.restaurant.name, "plan": PLANS.get(p.plan, {}).get("name", p.plan), "months": p.months, "amount": money(p.amount),
                      "reference": p.reference, "by": p.recorded_by, "age": seconds_since(p.paid_at, now)} for p in pays[:15]],
        "plans": PLANS,
    })


@require_POST
@platform_only
@json_api
def platform_action(request, pk, action):
    r = get_object_or_404(Restaurant, pk=pk)
    d = read_json(request)
    if action == "pay":
        services.record_plan_payment(r, d.get("plan") or r.plan, d.get("months", 1), d.get("amount"), d.get("reference"), request.user.username)
    elif action == "plan":
        services.set_plan(r, d.get("plan"))
    elif action == "suspend":
        services.set_suspended(r, True)
    elif action == "resume":
        services.set_suspended(r, False)
    elif action == "trial":
        services.extend_trial(r)
    else:
        raise services.OrderError("Unknown action.")
    return JsonResponse({"ok": True})


# ---------- login with lockout, health check ----------

LOGIN_TRIES, LOGIN_LOCK_SECONDS = 5, 15 * 60


class Login(LoginView):
    """Five wrong passwords for one username from one address locks that pair out for 15 minutes."""

    def _key(self):
        return "login-fail:%s:%s" % (self.request.META.get("REMOTE_ADDR", ""), self.request.POST.get("username", "").strip().lower())

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= LOGIN_TRIES:
            # an unbound form, so a locked-out attempt never checks the password
            return self.render_to_response(self.get_context_data(form=AuthenticationForm(request), locked=True), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        cache.set(key, cache.get(key, 0) + 1, LOGIN_LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
