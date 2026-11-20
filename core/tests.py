import json

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, TransactionTestCase

from . import services
from .models import Category, Dish, DishSize, Order, Restaurant, ServiceRequest, StaffProfile, Table
from .routing import websocket_urlpatterns


def make_restaurant(name):
    r = Restaurant.objects.create(name=name)
    cat = Category.objects.create(restaurant=r, name="Mains")
    curry = Dish.objects.create(restaurant=r, category=cat, name="Curry", price=500, station="Curry", prep_minutes=18)
    DishSize.objects.create(dish=curry, label="Half", price=300)
    DishSize.objects.create(dish=curry, label="Full", price=500)
    naan = Dish.objects.create(restaurant=r, category=cat, name="Naan", price=80, station="Tandoor", prep_minutes=8)
    table = Table.objects.create(restaurant=r, number=1)
    users = {}
    for role in ("owner", "waiter", "kitchen"):
        u = User.objects.create_user(f"{name}-{role}", password="pw")
        StaffProfile.objects.create(user=u, restaurant=r, role=role)
        users[role] = u
    return r, table, curry, naan, users


def lines(curry, naan, size=None):
    size = size or curry.sizes.get(label="Half")
    return [{"dish_id": curry.id, "size_id": size.id, "quantity": 2}, {"dish_id": naan.id, "quantity": 3}]


class OrderRules(TestCase):
    def setUp(self):
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")

    def test_server_computes_price_and_ignores_client_price(self):
        payload = lines(self.curry, self.naan)
        payload[0]["price"] = "1"  # a tampered price is ignored
        order = services.place_order(self.table, payload)
        self.assertEqual(order.total, 2 * 300 + 3 * 80)
        self.assertEqual(order.status, Order.PLACED)

    def test_rejects_bad_orders_and_saves_nothing(self):
        other, _, other_curry, _, _ = make_restaurant("B")
        bad = [
            [],
            [{"dish_id": self.naan.id, "quantity": 0}],
            [{"dish_id": self.naan.id, "quantity": 99}],
            [{"dish_id": other_curry.id, "size_id": other_curry.sizes.first().id, "quantity": 1}],  # another restaurant's dish
            [{"dish_id": self.curry.id, "quantity": 1}],  # sized dish with no size chosen
            [{"dish_id": "x", "quantity": 1}],
        ]
        for payload in bad:
            with self.assertRaises(services.OrderError, msg=payload):
                services.place_order(self.table, payload)
        self.naan.available = False
        self.naan.save()
        with self.assertRaises(services.OrderError):
            services.place_order(self.table, [{"dish_id": self.naan.id, "quantity": 1}])
        self.assertEqual(Order.objects.count(), 0)

    def test_status_flow_and_roles(self):
        order = services.place_order(self.table, lines(self.curry, self.naan))
        with self.assertRaises(PermissionDenied):  # kitchen cannot confirm an order
            services.advance_order(order, "kitchen")
        services.advance_order(order, "waiter")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COOKING)
        self.assertIsNotNone(order.cooking_at)
        with self.assertRaises(PermissionDenied):  # waiter cannot mark food ready
            services.advance_order(order, "waiter")
        services.advance_order(order, "kitchen")
        services.advance_order(order, "waiter")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SERVED)
        with self.assertRaises(services.OrderError):  # nothing after served
            services.advance_order(order, "waiter")

    def test_cannot_skip_ahead(self):
        order = services.place_order(self.table, lines(self.curry, self.naan))
        with self.assertRaises(PermissionDenied):
            services.mark_station_ready(order, "Curry", "waiter")
        with self.assertRaises(services.OrderError):  # still only placed
            services.mark_station_ready(order, "Curry", "kitchen")

    def test_order_ready_only_when_every_station_is_done(self):
        order = services.place_order(self.table, lines(self.curry, self.naan))
        services.advance_order(order, "waiter")
        services.mark_station_ready(order, "Curry", "kitchen")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COOKING)
        services.mark_station_ready(order, "Tandoor", "kitchen")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.READY)

    def test_service_request_flow_and_no_duplicates(self):
        first = services.call_waiter(self.table, "water")
        self.assertEqual(services.call_waiter(self.table, "water").pk, first.pk)
        self.assertEqual(ServiceRequest.objects.count(), 1)
        with self.assertRaises(services.OrderError):
            services.call_waiter(self.table, "dance")
        with self.assertRaises(PermissionDenied):
            services.update_request(first, "ack", "kitchen")
        services.update_request(first, "ack", "waiter")
        first.refresh_from_db()
        self.assertEqual(first.status, ServiceRequest.ACKNOWLEDGED)
        services.update_request(first, "done", "waiter")
        self.assertEqual(services.call_waiter(self.table, "water").status, ServiceRequest.OPEN)  # a new one after done


class Views(TestCase):
    def setUp(self):
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")
        self.r2, self.table2, self.curry2, self.naan2, self.users2 = make_restaurant("B")

    def post(self, client, path, data=None):
        return client.post(path, json.dumps(data or {}), content_type="application/json")

    def test_guest_flow_over_http(self):
        guest = Client(enforce_csrf_checks=False)
        page = guest.get(f"/t/{self.table.code}/")
        self.assertContains(page, "Curry")
        self.assertNotContains(page, "B Curry")
        res = self.post(guest, f"/t/{self.table.code}/order/", {"items": lines(self.curry, self.naan)})
        self.assertEqual(res.status_code, 200)
        state = guest.get(f"/t/{self.table.code}/state/").json()
        self.assertEqual(len(state["orders"]), 1)
        self.assertEqual(state["orders"][0]["total"], "840.00")
        self.assertEqual(self.post(guest, f"/t/{self.table.code}/order/", {"items": []}).status_code, 400)
        self.assertEqual(guest.get("/t/not-a-real-code/").status_code, 404)

    def test_menu_carries_dish_art_and_chefs_pick(self):
        self.curry.art, self.curry.featured = "makhani", True
        self.curry.save()
        page = Client().get(f"/t/{self.table.code}/")
        dishes = {d["name"]: d for c in page.context["menu"]["categories"] for d in c["dishes"]}
        self.assertEqual((dishes["Curry"]["art"], dishes["Curry"]["featured"]), ("makhani", True))
        self.assertEqual((dishes["Naan"]["art"], dishes["Naan"]["featured"]), ("", False))

    def test_kitchen_ticket_has_a_target_time_from_the_dish_prep_time(self):
        order = services.place_order(self.table, lines(self.curry, self.naan))
        services.advance_order(order, "waiter")
        kitchen = Client()
        kitchen.force_login(self.users["kitchen"])
        stations = {s["name"]: s["tickets"][0] for s in kitchen.get("/kitchen/data/").json()["stations"]}
        self.assertEqual(stations["Curry"]["target"], 18 * 60)
        self.assertEqual(stations["Tandoor"]["target"], 8 * 60)

    def test_guest_csrf_is_enforced(self):
        strict = Client(enforce_csrf_checks=True)
        res = strict.post(f"/t/{self.table.code}/call/", json.dumps({"kind": "water"}), content_type="application/json")
        self.assertEqual(res.status_code, 403)

    def test_guests_only_see_their_own_table(self):
        t2 = Table.objects.create(restaurant=self.r, number=2)
        services.place_order(self.table, lines(self.curry, self.naan))
        self.assertEqual(Client().get(f"/t/{t2.code}/state/").json()["orders"], [])

    def test_staff_screens_need_login_and_the_right_role(self):
        self.assertEqual(Client().get("/staff/").status_code, 302)
        self.assertEqual(Client().get("/staff/data/").status_code, 403)
        kitchen = Client()
        kitchen.force_login(self.users["kitchen"])
        self.assertEqual(kitchen.get("/kitchen/").status_code, 200)
        self.assertEqual(kitchen.get("/staff/").status_code, 403)
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        self.assertEqual(waiter.get("/staff/").status_code, 200)
        self.assertEqual(waiter.get("/kitchen/").status_code, 403)
        self.assertEqual(waiter.get("/staff/qr/").status_code, 403)

    def test_one_restaurant_cannot_see_or_change_another(self):
        mine = services.place_order(self.table, lines(self.curry, self.naan))
        theirs = services.place_order(self.table2, lines(self.curry2, self.naan2))
        req = services.call_waiter(self.table2, "bill")
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        data = waiter.get("/staff/data/").json()
        self.assertEqual([o["id"] for o in data["orders"]], [mine.id])
        self.assertEqual(data["requests"], [])
        self.assertEqual(self.post(waiter, f"/staff/orders/{theirs.id}/advance/").status_code, 404)
        self.assertEqual(self.post(waiter, f"/staff/requests/{req.id}/ack/").status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Order.PLACED)

    def test_full_flow_over_http(self):
        order = services.place_order(self.table, lines(self.curry, self.naan))
        waiter, kitchen = Client(), Client()
        waiter.force_login(self.users["waiter"])
        kitchen.force_login(self.users["kitchen"])
        self.assertEqual(waiter.get("/staff/data/").json()["orders"][0]["action"], "Send to kitchen")
        self.assertEqual(self.post(waiter, f"/staff/orders/{order.id}/advance/").status_code, 200)
        board = kitchen.get("/kitchen/data/").json()["stations"]
        self.assertEqual([s["name"] for s in board], ["Curry", "Tandoor"])
        self.assertEqual(self.post(waiter, f"/kitchen/orders/{order.id}/ready/", {"station": "Curry"}).status_code, 403)
        for station in ("Curry", "Tandoor"):
            self.assertEqual(self.post(kitchen, f"/kitchen/orders/{order.id}/ready/", {"station": station}).status_code, 200)
        self.assertEqual(kitchen.get("/kitchen/data/").json()["stations"], [])
        self.assertEqual(waiter.get("/staff/data/").json()["orders"][0]["action"], "Mark served")
        self.assertEqual(self.post(waiter, f"/staff/orders/{order.id}/advance/").status_code, 200)
        self.assertEqual(waiter.get("/staff/data/").json()["orders"], [])

    def test_owner_qr_sheet_has_a_code_per_table(self):
        owner = Client()
        owner.force_login(self.users["owner"])
        page = owner.get("/staff/qr/")
        self.assertContains(page, "<svg")
        self.assertContains(page, self.table.code)
        self.assertNotContains(page, self.table2.code)


class LiveUpdates(TransactionTestCase):
    """Placing an order pushes a message to the table, the restaurant's staff, and nobody else."""

    async def test_order_reaches_guest_and_staff_but_not_other_restaurants(self):
        r, table, curry, naan, users = await sync_to_async(make_restaurant)("A")
        _, table_b, _, _, users_b = await sync_to_async(make_restaurant)("B")
        app = URLRouter(websocket_urlpatterns)

        guest = WebsocketCommunicator(app, f"/ws/t/{table.code}/")
        staff = WebsocketCommunicator(app, "/ws/staff/")
        staff.scope["user"] = users["waiter"]
        other_staff = WebsocketCommunicator(app, "/ws/staff/")
        other_staff.scope["user"] = users_b["waiter"]
        stranger = WebsocketCommunicator(app, "/ws/staff/")
        from django.contrib.auth.models import AnonymousUser
        stranger.scope["user"] = AnonymousUser()
        for c in (guest, staff, other_staff):
            self.assertTrue((await c.connect())[0])
        self.assertFalse((await stranger.connect())[0])  # no login, no socket

        await sync_to_async(lambda: services.place_order(table, lines(curry, naan)))()
        self.assertEqual(json.loads(await guest.receive_from()), {"changed": True})
        self.assertEqual(json.loads(await staff.receive_from()), {"changed": True})
        self.assertTrue(await other_staff.receive_nothing())
        for c in (guest, staff, other_staff):
            await c.disconnect()
