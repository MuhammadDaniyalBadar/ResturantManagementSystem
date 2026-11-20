import json
from datetime import timedelta
from functools import wraps

import segno
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import Category, Order, ServiceRequest, StaffProfile, Table

OWNER, WAITER, KITCHEN = StaffProfile.OWNER, StaffProfile.WAITER, StaffProfile.KITCHEN


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
    if order.status == Order.COOKING and order.cooking_at:
        estimate = max((i.dish.prep_minutes for i in items), default=0) * 60
        left = max(0, estimate - seconds_since(order.cooking_at, now))
    return {
        "id": order.id,
        "table": order.table.number,
        "status": order.status,
        "label": order.get_status_display(),
        "seconds_left": left,
        "seconds_old": seconds_since(order.created_at, now),
        "total": str(order.total),
        "items": [
            {"name": i.dish.name, "size": i.size_label, "qty": i.quantity, "note": i.note, "station": i.dish.station, "ready": bool(i.ready_at)}
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
    }


def orders_qs(**filters):
    return Order.objects.filter(**filters).select_related("table").prefetch_related("items__dish")


# ---------- home ----------

def home(request):
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
    if profile:
        return redirect("kitchen" if profile.role == KITCHEN else "staff")
    return render(request, "home.html")


# ---------- guest ----------

def get_table(code):
    return get_object_or_404(Table.objects.select_related("restaurant"), code=code)


def menu_data(restaurant):
    categories = []
    for category in Category.objects.filter(restaurant=restaurant).prefetch_related("dish_set__sizes"):
        dishes = [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "price": str(d.price),
                "image": d.image_url,
                "allergens": [a.strip() for a in d.allergens.split(",") if a.strip()],
                "spice": d.get_spice_display() if d.spice else "",
                "sizes": [{"id": s.id, "label": s.label, "price": str(s.price)} for s in d.sizes.all()],
            }
            for d in category.dish_set.all()
            if d.available
        ]
        if dishes:
            categories.append({"name": category.name, "dishes": dishes})
    return {"currency": restaurant.currency, "categories": categories}


@require_GET
def table_page(request, code):
    table = get_table(code)
    return render(request, "guest.html", {"table": table, "restaurant": table.restaurant, "menu": menu_data(table.restaurant)})


@require_GET
def table_state(request, code):
    table = get_table(code)
    now = timezone.now()
    recent = now - timedelta(hours=6)  # an earlier party's orders do not show up for the next guests
    orders = orders_qs(table=table, created_at__gte=recent).exclude(status=Order.SERVED)
    requests = ServiceRequest.objects.filter(table=table).exclude(status=ServiceRequest.DONE).select_related("table")
    return JsonResponse({"orders": [order_json(o, now) for o in orders], "requests": [request_json(r, now) for r in requests]})


@require_POST
@json_api
def table_order(request, code):
    table = get_table(code)
    order = services.place_order(table, read_json(request).get("items"))
    return JsonResponse({"ok": True, "order_id": order.id})


@require_POST
@json_api
def table_call(request, code):
    table = get_table(code)
    req = services.call_waiter(table, read_json(request).get("kind"))
    return JsonResponse({"ok": True, "request_id": req.id})


# ---------- staff ----------

@staff_only(WAITER, OWNER)
def staff_page(request):
    return render(request, "staff.html", {"restaurant": request.profile.restaurant, "role": request.profile.role})


@require_GET
@staff_only(api=True)
def staff_data(request):
    restaurant, role, now = request.profile.restaurant, request.profile.role, timezone.now()
    orders = []
    for o in orders_qs(restaurant=restaurant).exclude(status=Order.SERVED):
        data = order_json(o, now)
        nxt = services.NEXT.get(o.status)
        data["action"] = {"cooking": "Send to kitchen", "served": "Mark served"}.get(nxt) if role in services.WHO.get(nxt, ()) else None
        orders.append(data)
    reqs = ServiceRequest.objects.filter(restaurant=restaurant).exclude(status=ServiceRequest.DONE).select_related("table")
    return JsonResponse({"orders": orders, "requests": [request_json(r, now) for r in reqs]})


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
    services.update_request(req, action, request.profile.role)
    return JsonResponse({"ok": True})


# ---------- kitchen ----------

@staff_only(KITCHEN, OWNER)
def kitchen_page(request):
    return render(request, "kitchen.html", {"restaurant": request.profile.restaurant})


@require_GET
@staff_only(KITCHEN, OWNER, api=True)
def kitchen_data(request):
    now = timezone.now()
    stations = {}
    for o in orders_qs(restaurant=request.profile.restaurant, status=Order.COOKING).order_by("cooking_at"):
        data = order_json(o, now)
        for station in sorted({i["station"] for i in data["items"]}):
            lines = [i for i in data["items"] if i["station"] == station]
            if all(i["ready"] for i in lines):
                continue
            ticket = {"order_id": o.id, "table": o.table.number, "elapsed": seconds_since(o.cooking_at, now), "items": lines}
            stations.setdefault(station, []).append(ticket)
    return JsonResponse({"stations": [{"name": n, "tickets": t} for n, t in sorted(stations.items())]})


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
        {"number": t.number, "url": url, "svg": segno.make(url, error="m").svg_inline(scale=5, border=1)}
        for t in Table.objects.filter(restaurant=request.profile.restaurant)
        for url in [request.build_absolute_uri(reverse("table", args=[t.code]))]
    ]
    return render(request, "qr.html", {"restaurant": request.profile.restaurant, "tables": tables})
