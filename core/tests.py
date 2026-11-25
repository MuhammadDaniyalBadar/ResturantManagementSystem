import json
from datetime import date, timedelta

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
    r = Restaurant.objects.create(name=name, slug=name.lower(), paid_until=date.today() + timedelta(days=30))
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
        stations = {s["name"]: s["tickets"][0] for s in kitchen.get("/kitchen/data/").json()["stations"] if s["tickets"]}
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
        self.assertEqual([s["name"] for s in board if s["tickets"]], ["Curry", "Tandoor"])
        self.assertEqual(self.post(waiter, f"/kitchen/orders/{order.id}/ready/", {"station": "Curry"}).status_code, 403)
        for station in ("Curry", "Tandoor"):
            self.assertEqual(self.post(kitchen, f"/kitchen/orders/{order.id}/ready/", {"station": station}).status_code, 200)
        self.assertEqual([s for s in kitchen.get("/kitchen/data/").json()["stations"] if s["tickets"]], [])
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


from decimal import Decimal  # noqa: E402

from django.core import mail  # noqa: E402

from .models import Bill, Deal, Guest, Member, Payment, QuizQuestion, Review  # noqa: E402


class Dining(TestCase):
    """The reel's flow after the order: shared tables, deals, bill split, tip, paying, rewards, reviews, quiz, floor, owner."""

    def setUp(self):
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")
        self.r.reward_rate = 10
        self.r.save()
        self.lassi = Dish.objects.create(restaurant=self.r, category=self.curry.category, name="Lassi", price=180, station="Drinks", prep_minutes=4)
        self.a = services.join_guest(self.table, "Aman")
        self.b = services.join_guest(self.table, "Riya")

    def ordered(self):
        order = services.place_order(self.table, lines(self.curry, self.naan), self.a)  # 2 x 300 + 3 x 80 = 840
        return order, order.bill

    def test_bill_adds_service_and_tax_then_splits(self):
        _, bill = self.ordered()
        s = services.bill_summary(bill)
        self.assertEqual((s["sub"], s["service"], s["tax"], s["base"]), (840, 42, 88, 970))
        bill.split = 2
        self.assertEqual(services.bill_summary(bill)["share"], 485)

    def test_two_guests_share_one_bill_and_pay_their_halves_with_tips(self):
        _, bill = self.ordered()
        services.request_payment(bill, self.a, "card", 10, "aman@example.com", 2)
        with self.assertRaises(services.OrderError):
            services.request_payment(bill, self.a, "cash", 0, "", 2)  # one choice each
        services.request_payment(bill, self.b, "apple", 0, "", 5)  # the first person's split holds
        bill.refresh_from_db()
        self.assertEqual((bill.status, bill.split), (Bill.PAYING, 2))
        first, second = bill.payments.order_by("id")
        self.assertEqual((first.share, first.tip, first.amount, second.amount), (485, 49, 534, 485))
        with self.assertRaises(services.OrderError):  # a third share does not exist
            services.request_payment(bill, services.join_guest(self.table, "Third"), "cash", 0, "", 2)
        with self.assertRaises(PermissionDenied):
            services.confirm_payment(first, "kitchen")
        services.confirm_payment(first, "waiter")
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.PAYING)
        services.confirm_payment(second, "waiter")
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.PAID)
        self.assertIsNotNone(bill.paid_at)
        self.assertEqual(ServiceRequest.objects.filter(table=self.table).exclude(status="done").count(), 0)
        self.assertEqual(len(mail.outbox), 1)  # only the guest who gave an email
        self.assertIn("Bill total  Rs 970", mail.outbox[0].body)
        self.assertIn("Paid by Card  Rs 534", mail.outbox[0].body)
        self.assertNotEqual(services.get_bill(self.table).pk, bill.pk)  # the next party gets a fresh bill

    def test_rewards_go_to_the_member_when_the_bill_closes(self):
        _, bill = self.ordered()
        member = services.join_member(self.r, "Aman@Example.com")
        self.assertEqual(member.email, "aman@example.com")
        services.request_payment(bill, self.a, "cash", 0, "", 1, member)
        services.close_bill_by_staff(bill, "waiter")
        member.refresh_from_db()
        self.assertEqual(member.points, 97)  # 970 spent at 10 points per 100
        with self.assertRaises(services.OrderError):
            services.join_member(self.r, "not an email")

    def test_payment_rules(self):
        with self.assertRaises(services.OrderError):
            services.request_payment(services.get_bill(self.table), self.a, "card", 0, "", 1)  # nothing ordered yet
        _, bill = self.ordered()
        for method, tip, split in [("bitcoin", 0, 1), ("cash", 99, 1), ("cash", 0, 0), ("cash", 0, 50)]:
            with self.assertRaises(services.OrderError, msg=(method, tip, split)):
                services.request_payment(bill, self.a, method, tip, "", split)
        services.request_payment(bill, self.a, "cash", 0, "", 1)
        with self.assertRaises(services.OrderError):  # the bill is being paid, so no new orders
            services.place_order(self.table, lines(self.curry, self.naan), self.b)

    def test_deal_price_needs_the_main_dish_in_the_same_order(self):
        deal = Deal.objects.create(restaurant=self.r, dish=self.curry, partner=self.lassi, price=99)
        with self.assertRaises(services.OrderError):
            services.place_order(self.table, [{"dish_id": self.lassi.id, "deal_id": deal.id, "quantity": 1}])
        with self.assertRaises(services.OrderError):  # more deal drinks than main dishes
            services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": self.curry.sizes.first().id, "quantity": 1}, {"dish_id": self.lassi.id, "deal_id": deal.id, "quantity": 2}])
        order = services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": self.curry.sizes.get(label="Half").id, "quantity": 2}, {"dish_id": self.lassi.id, "deal_id": deal.id, "quantity": 2}])
        self.assertEqual(order.total, 2 * 300 + 2 * 99)  # not 180 each
        self.assertEqual(Order.objects.count(), 1)

    def test_review_only_after_choosing_to_pay_and_only_once(self):
        _, bill = self.ordered()
        guest_client = Client()
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/review/", {"stars": 5}).status_code, 400)  # no guest yet
        self.post(guest_client, f"/t/{self.table.code}/join/", {"name": "Aman"})
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/review/", {"stars": 5}).status_code, 400)  # not paid yet
        self.post(guest_client, f"/t/{self.table.code}/order/", {"items": lines(self.curry, self.naan)})
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/pay/", {"method": "cash", "tip": 0, "split": 1, "email": ""}).status_code, 200)
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/review/", {"stars": 9}).status_code, 400)
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/review/", {"stars": 4, "comment": "Lovely"}).status_code, 200)
        self.assertEqual(self.post(guest_client, f"/t/{self.table.code}/review/", {"stars": 5}).status_code, 400)
        self.assertEqual(Review.objects.count(), 1)

    def post(self, client, path, data=None):
        return client.post(path, json.dumps(data or {}), content_type="application/json")

    def test_guests_join_by_session_and_their_orders_carry_their_name(self):
        c = Client()
        self.assertEqual(self.post(c, f"/t/{self.table.code}/join/", {"name": "  "}).status_code, 400)
        self.assertEqual(self.post(c, f"/t/{self.table.code}/join/", {"name": "Riya"}).status_code, 200)
        self.post(c, f"/t/{self.table.code}/order/", {"items": lines(self.curry, self.naan)})
        state = c.get(f"/t/{self.table.code}/state/").json()
        self.assertEqual(state["guest"]["name"], "Riya")
        self.assertEqual(state["orders"][0]["guest"], "Riya")
        self.assertEqual(Client().get(f"/t/{self.table.code}/state/").json()["guest"], None)  # another phone is not Riya

    def test_quiz_scores_players_and_tables_and_each_question_counts_once(self):
        QuizQuestion.objects.create(restaurant=self.r, text="Q1", options=["a", "b"], answer=1)
        QuizQuestion.objects.create(restaurant=self.r, text="Q2", options=["a", "b"], answer=0)
        q1 = services.next_question(self.a)
        self.assertEqual(q1.text, "Q1")
        self.assertEqual(services.answer_question(self.a, q1.id, 1), 1)
        with self.assertRaises(services.OrderError):
            services.answer_question(self.a, q1.id, 1)
        self.assertEqual(services.next_question(self.a).text, "Q2")
        services.answer_question(self.b, q1.id, 0)  # wrong
        other = Table.objects.create(restaurant=self.r, number=2)
        c = services.join_guest(other, "Sam")
        services.answer_question(c, q1.id, 1)
        board = services.leaderboard(self.r)
        self.assertEqual([(t["table"], t["points"]) for t in board["tables"]], [(1, 10), (2, 10)])
        self.assertEqual({p["name"] for p in board["players"]}, {"Aman", "Sam"})

    def test_floor_status_follows_the_table_through_a_meal(self):
        def status():
            return next(t for t in services_floor() if t["n"] == 1)["status"]

        from .views import floor_json
        from django.utils import timezone

        def services_floor():
            return floor_json(self.r, timezone.now())

        self.assertEqual(status(), "free")
        order, bill = self.ordered()
        self.assertEqual(status(), "ordering")
        services.advance_order(order, "waiter")
        self.assertEqual(status(), "eating")
        services.call_waiter(self.table, "water")
        self.assertEqual(status(), "help")
        ServiceRequest.objects.update(status="done")
        services.request_payment(bill, self.a, "cash", 0, "", 1)
        self.assertEqual(status(), "bill")
        services.close_bill_by_staff(bill, "waiter")
        self.assertEqual(status(), "free")

    def test_waiter_can_assign_a_request_and_the_response_time_is_recorded(self):
        req = services.call_waiter(self.table, "call")
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        self.assertEqual(self.post(waiter, f"/staff/requests/{req.id}/assign/", {"user_id": self.users["waiter"].id}).status_code, 200)
        self.assertEqual(self.post(waiter, f"/staff/requests/{req.id}/ack/").status_code, 200)
        data = waiter.get("/staff/data/").json()
        self.assertEqual(data["requests"][0]["assigned_id"], self.users["waiter"].id)
        self.assertIsNotNone(data["requests"][0]["response"])
        other = make_restaurant("B")[4]["waiter"]
        self.assertEqual(self.post(waiter, f"/staff/requests/{req.id}/assign/", {"user_id": other.id}).status_code, 404)  # not this restaurant's staff

    def test_staff_confirm_payment_over_http_and_only_in_their_own_restaurant(self):
        _, bill = self.ordered()
        services.request_payment(bill, self.a, "cash", 0, "", 1)
        pay = Payment.objects.get()
        stranger = Client()
        stranger.force_login(make_restaurant("B")[4]["waiter"])
        self.assertEqual(self.post(stranger, f"/staff/payments/{pay.id}/paid/").status_code, 404)
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        self.assertEqual(waiter.get("/staff/data/").json()["payments"][0]["amount"], "970.00")
        self.assertEqual(self.post(waiter, f"/staff/payments/{pay.id}/paid/").status_code, 200)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.PAID)

    def test_owner_overview_is_for_the_owner_and_counts_tonights_sales(self):
        _, bill = self.ordered()
        services.request_payment(bill, self.a, "cash", 0, "", 1)
        services.close_bill_by_staff(bill, "waiter")
        Review.objects.create(bill=bill, guest=self.a, stars=4, comment="Good")
        waiter, owner = Client(), Client()
        waiter.force_login(self.users["waiter"])
        owner.force_login(self.users["owner"])
        self.assertEqual(waiter.get("/owner/data/").status_code, 403)
        self.assertEqual(waiter.get("/owner/").status_code, 403)
        d = owner.get("/owner/data/").json()
        self.assertEqual((d["sales_today"], d["orders_today"], d["rating"], d["review_count"]), ("970.00", 1, 4.0, 1))
        self.assertEqual(d["best"][0]["qty"], 3)
        self.assertEqual(owner.get("/owner/").status_code, 200)


class BetaFoundation(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")

    def test_health_check(self):
        self.assertEqual(Client().get("/healthz/").content, b"ok")

    def test_five_wrong_passwords_lock_the_login_even_for_the_right_one(self):
        c, name = Client(), self.users["waiter"].username
        for _ in range(5):
            self.assertEqual(c.post("/accounts/login/", {"username": name, "password": "wrong"}).status_code, 200)
        locked = c.post("/accounts/login/", {"username": name, "password": "pw"})
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, "Too many wrong passwords", status_code=429)
        self.assertNotIn("_auth_user_id", c.session)
        # another user from the same address is not locked
        self.assertEqual(c.post("/accounts/login/", {"username": self.users["owner"].username, "password": "pw"}).status_code, 302)

    def test_right_password_resets_the_count(self):
        c, name = Client(), self.users["waiter"].username
        for _ in range(4):
            c.post("/accounts/login/", {"username": name, "password": "wrong"})
        self.assertEqual(c.post("/accounts/login/", {"username": name, "password": "pw"}).status_code, 302)
        c.logout()
        for _ in range(4):
            c.post("/accounts/login/", {"username": name, "password": "wrong"})
        self.assertEqual(c.post("/accounts/login/", {"username": name, "password": "pw"}).status_code, 302)

    def test_uploaded_photo_replaces_the_illustration_on_menu_and_tickets(self):
        import shutil, tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
        with override_settings(MEDIA_ROOT=tmp):
            self.curry.art = "makhani"
            self.curry.photo = SimpleUploadedFile("curry.png", png, content_type="image/png")
            self.curry.full_clean()
            self.curry.save()
            menu = Client().get(f"/t/{self.table.code}/").context["menu"]
            arts = {d["name"]: d["art"] for c in menu["categories"] for d in c["dishes"]}
            self.assertTrue(arts["Curry"].startswith("/media/dishes/curry"))
            self.assertEqual(arts["Naan"], "")
            self.assertEqual(Client().get(arts["Curry"]).status_code, 200)

    def test_photo_over_5mb_is_rejected(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import max_photo_size
        with self.assertRaises(ValidationError):
            max_photo_size(SimpleUploadedFile("big.png", b"0" * (5 * 1024 * 1024 + 1)))


class Forbidden(TestCase):
    def test_wrong_role_gets_a_helpful_page_with_switch_user(self):
        r, table, curry, naan, users = make_restaurant("A")
        c = Client()
        c.force_login(users["kitchen"])
        page = c.get("/staff/qr/")
        self.assertEqual(page.status_code, 403)
        self.assertContains(page, "Log out and switch user", status_code=403)
        self.assertContains(page, users["kitchen"].username, status_code=403)
        # logging out from there lands on the login page, which returns to the QR page
        out = c.post("/accounts/logout/", {"next": "/accounts/login/?next=/staff/qr/"})
        self.assertEqual(out.url, "/accounts/login/?next=/staff/qr/")


class Beta2(TestCase):
    """Order edits and cancels, sold-out dishes, team management, and the owner's admin scoped to one restaurant."""

    def setUp(self):
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")
        self.a = services.join_guest(self.table, "Aman")
        self.b = services.join_guest(self.table, "Riya")

    def post(self, client, path, data=None):
        return client.post(path, json.dumps(data or {}), content_type="application/json")

    def staff(self, role="waiter"):
        c = Client()
        c.force_login(self.users[role])
        return c

    def guest_client(self, name="Aman"):
        c = Client()
        self.post(c, f"/t/{self.table.code}/join/", {"name": name})
        return c

    # --- cancel and edit ---

    def test_staff_cancel_takes_the_order_off_the_bill_and_the_kitchen(self):
        keep = services.place_order(self.table, [{"dish_id": self.naan.id, "quantity": 1}], self.a)
        order = services.place_order(self.table, lines(self.curry, self.naan), self.a)
        services.advance_order(order, "waiter")
        self.assertEqual(self.post(self.staff(), f"/staff/orders/{order.id}/cancel/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.bill_id, order.cancelled_by), (Order.CANCELLED, None, self.users["waiter"].username))
        self.assertEqual(services.bill_summary(keep.bill)["sub"], 80)
        board = self.staff("kitchen").get("/kitchen/data/").json()["stations"]
        self.assertEqual([s for s in board if s["tickets"]], [])
        self.assertEqual([o["id"] for o in self.staff().get("/staff/data/").json()["orders"]], [keep.id])
        self.assertEqual(self.post(self.staff("kitchen"), f"/staff/orders/{keep.id}/cancel/").status_code, 403)

    def test_guest_cancels_only_their_own_order_and_only_before_the_kitchen(self):
        aman, riya = self.guest_client("Aman"), self.guest_client("Riya")
        oid = self.post(aman, f"/t/{self.table.code}/order/", {"items": [{"dish_id": self.naan.id, "quantity": 2}]}).json()["order_id"]
        self.assertEqual(self.post(riya, f"/t/{self.table.code}/orders/{oid}/cancel/").status_code, 400)
        self.assertEqual(self.post(Client(), f"/t/{self.table.code}/orders/{oid}/cancel/").status_code, 400)
        self.assertEqual(self.post(aman, f"/t/{self.table.code}/orders/{oid}/cancel/").status_code, 200)
        self.assertEqual(Order.objects.get(pk=oid).cancelled_by, "guest")
        self.assertEqual(aman.get(f"/t/{self.table.code}/state/").json()["orders"], [])
        oid = self.post(aman, f"/t/{self.table.code}/order/", {"items": [{"dish_id": self.naan.id, "quantity": 1}]}).json()["order_id"]
        services.advance_order(Order.objects.get(pk=oid), "waiter")
        self.assertEqual(self.post(aman, f"/t/{self.table.code}/orders/{oid}/cancel/").status_code, 400)

    def test_no_changes_once_served_or_once_the_bill_is_being_paid(self):
        order = services.place_order(self.table, lines(self.curry, self.naan), self.a)
        services.request_payment(order.bill, self.a, "cash", 0, "", 1)
        with self.assertRaises(services.OrderError):
            services.cancel_order(order, "waiter")
        with self.assertRaises(services.OrderError):
            services.remove_item(order.items.first(), "waiter")
        served = services.place_order(Table.objects.create(restaurant=self.r, number=2), [{"dish_id": self.naan.id, "quantity": 1}])
        for _ in range(3):
            services.advance_order(served, "owner")
        with self.assertRaises(services.OrderError):
            services.cancel_order(served, "waiter")

    def test_removing_items_updates_the_total_and_the_last_one_cancels(self):
        from .models import Deal, OrderItem
        lassi = Dish.objects.create(restaurant=self.r, category=self.curry.category, name="Lassi", price=180, station="Drinks")
        deal = Deal.objects.create(restaurant=self.r, dish=self.curry, partner=lassi, price=99)
        half = self.curry.sizes.get(label="Half")
        order = services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": half.id, "quantity": 1},
                                                  {"dish_id": lassi.id, "deal_id": deal.id, "quantity": 1},
                                                  {"dish_id": self.naan.id, "quantity": 2}], self.a)
        curry_line, lassi_line, naan_line = order.items.order_by("id")
        waiter = self.staff()
        r = self.post(waiter, f"/staff/items/{curry_line.id}/remove/")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "Remove the deal item that goes with this dish first."))
        for line in (lassi_line, curry_line):
            self.assertEqual(self.post(waiter, f"/staff/items/{line.id}/remove/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.total, order.status), (160, Order.PLACED))
        self.assertEqual(self.post(waiter, f"/staff/items/{naan_line.id}/remove/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.bill_id), (Order.CANCELLED, None))
        other, _, _, other_naan, _ = make_restaurant("B")
        other_line = services.place_order(other.tables.first(), [{"dish_id": other_naan.id, "quantity": 1}]).items.first()
        self.assertEqual(self.post(waiter, f"/staff/items/{other_line.id}/remove/").status_code, 404)
        self.assertFalse(OrderItem.objects.filter(pk=curry_line.pk).exists())

    def test_owner_numbers_skip_cancelled_orders(self):
        services.cancel_order(services.place_order(self.table, lines(self.curry, self.naan), self.a), "waiter")
        services.place_order(self.table, [{"dish_id": self.naan.id, "quantity": 1}], self.a)
        data = self.staff("owner").get("/owner/data/").json()
        self.assertEqual(data["orders_today"], 1)
        self.assertEqual([(b["name"], b["qty"]) for b in data["best"]], [("Naan", 1)])

    # --- sold out ---

    def test_sold_out_shows_live_to_guests_and_blocks_orders(self):
        guest = self.guest_client()
        self.assertEqual(self.post(self.staff("kitchen"), f"/staff/dishes/{self.naan.id}/available/", {"available": False}).status_code, 200)
        state = guest.get(f"/t/{self.table.code}/state/").json()
        self.assertEqual(state["sold_out"], [self.naan.id])
        menu = guest.get(f"/t/{self.table.code}/").context["menu"]
        self.assertIn({"Naan": False}, [{d["name"]: d["available"]} for c in menu["categories"] for d in c["dishes"]])
        r = self.post(guest, f"/t/{self.table.code}/order/", {"items": [{"dish_id": self.naan.id, "quantity": 1}]})
        self.assertEqual(r.status_code, 400)
        self.post(self.staff(), f"/staff/dishes/{self.naan.id}/available/", {"available": True})
        self.assertEqual(guest.get(f"/t/{self.table.code}/state/").json()["sold_out"], [])
        other_dish = make_restaurant("B")[2]
        self.assertEqual(self.post(self.staff(), f"/staff/dishes/{other_dish.id}/available/", {"available": False}).status_code, 404)
        self.assertEqual(self.post(Client(), f"/staff/dishes/{self.naan.id}/available/", {"available": False}).status_code, 403)

    # --- team ---

    def test_owner_adds_a_teammate_who_can_then_log_in(self):
        owner = self.staff("owner")
        bad = [({"username": "x", "role": "waiter", "password": "Str0ng-pass"}, "Usernames"),
               ({"username": "newbie", "role": "chef", "password": "Str0ng-pass"}, "Pick a role"),
               ({"username": "newbie", "role": "waiter", "password": "12345678"}, "too common"),
               ({"username": "newbie", "role": "waiter", "password": "short"}, "too short"),
               ({"username": self.users["waiter"].username.upper(), "role": "waiter", "password": "Str0ng-pass"}, "taken")]
        for body, msg in bad:
            r = self.post(owner, "/owner/team/data/", body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(msg, r.json()["error"])
        r = self.post(owner, "/owner/team/data/", {"username": "Newbie", "name": "New Bie", "role": "kitchen", "password": "Str0ng-pass"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("newbie", [u["username"] for u in r.json()["team"]])
        c = Client()
        self.assertEqual(c.post("/accounts/login/", {"username": "newbie", "password": "Str0ng-pass"}).status_code, 302)
        self.assertEqual(c.get("/kitchen/").status_code, 200)
        self.assertEqual(c.get("/staff/").status_code, 403)
        self.assertEqual(self.post(self.staff(), "/owner/team/data/", {"username": "sneaky", "role": "owner", "password": "Str0ng-pass"}).status_code, 403)

    def test_switching_a_login_off_or_resetting_it_ends_their_sessions(self):
        owner, waiter, me = self.staff("owner"), self.staff(), self.users["owner"]
        wid = self.users["waiter"].id
        self.assertEqual(self.post(owner, f"/owner/team/{wid}/", {"active": False}).status_code, 200)
        self.assertEqual(waiter.get("/staff/").status_code, 302)  # signed out
        self.post(owner, f"/owner/team/{wid}/", {"active": True})
        waiter = self.staff()
        self.assertEqual(self.post(owner, f"/owner/team/{wid}/", {"password": "An0ther-pass"}).status_code, 200)
        self.assertEqual(waiter.get("/staff/").status_code, 302)
        self.assertEqual(self.post(owner, f"/owner/team/{me.id}/", {"active": False}).status_code, 400)
        self.assertEqual(self.post(owner, f"/owner/team/{me.id}/", {"role": "waiter"}).status_code, 400)
        outsider = make_restaurant("B")[4]["waiter"]
        self.assertEqual(self.post(owner, f"/owner/team/{outsider.id}/", {"active": False}).status_code, 404)

    def test_role_change_grants_and_removes_admin_access(self):
        owner, w = self.staff("owner"), self.users["waiter"]
        self.post(owner, f"/owner/team/{w.id}/", {"role": "owner"})
        w.refresh_from_db()
        self.assertTrue(w.is_staff and w.has_perm("core.change_dish"))
        self.post(owner, f"/owner/team/{w.id}/", {"role": "waiter"})
        w = User.objects.get(pk=w.pk)  # fresh object: permissions are cached per instance
        self.assertFalse(w.is_staff or w.has_perm("core.change_dish"))

    # --- admin scoped to one restaurant ---

    def test_owner_admin_shows_only_their_restaurant(self):
        services.set_access(self.users["owner"], "owner")
        other, other_table, other_curry, _, _ = make_restaurant("B")
        owner = self.staff("owner")
        page = owner.get("/admin/core/dish/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual({d.restaurant_id for d in page.context["cl"].queryset}, {self.r.id})
        self.assertEqual(owner.get(f"/admin/core/dish/{other_curry.id}/change/").status_code, 302)  # not found for them
        form = owner.get("/admin/core/dish/add/").context["adminform"].form
        self.assertNotIn("restaurant", form.fields)
        self.assertEqual(set(form.fields["category"].queryset), {self.curry.category})
        r = owner.post("/admin/core/table/add/", {"number": 9, "x": 50, "y": 50})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Table.objects.get(number=9).restaurant, self.r)
        self.assertEqual([x.id for x in owner.get("/admin/core/restaurant/").context["cl"].queryset], [self.r.id])
        self.assertEqual(owner.get("/admin/core/restaurant/add/").status_code, 403)
        self.assertEqual(owner.get("/admin/core/staffprofile/").status_code, 403)
        req_form = owner.get("/admin/core/servicerequest/add/").context["adminform"].form
        self.assertEqual({u.profile.restaurant_id for u in req_form.fields["assigned"].queryset}, {self.r.id})


class Beta3(TestCase):
    """Signup, plans and limits, the platform console, branding, reservations."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")

    def post(self, client, path, data=None):
        return client.post(path, json.dumps(data or {}), content_type="application/json")

    def signup(self, c=None, **over):
        data = {"restaurant": "Karachi Grill", "name": "Hina", "username": "hina", "email": "hina@grill.pk", "password": "Grill-Pass-77", "tables": "5"}
        data.update(over)
        return (c or Client()).post("/signup/", data)

    def test_signup_creates_a_restaurant_on_trial_with_owner_and_tables(self):
        c = Client()
        r = self.signup(c)
        self.assertEqual(r.status_code, 302)
        rest = Restaurant.objects.get(name="Karachi Grill")
        self.assertEqual((rest.status, rest.plan, rest.slug, rest.tables.count(), rest.days_left), ("trial", "pro", "karachi-grill", 5, 14))
        owner = User.objects.get(username="hina")
        self.assertEqual((owner.profile.restaurant, owner.profile.role, owner.email, owner.is_staff), (rest, "owner", "hina@grill.pk", True))
        data = c.get("/owner/data/").json()
        self.assertEqual([s["done"] for s in data["setup"]], [False, False, False, False])
        self.assertEqual(c.get("/admin/core/dish/").context["cl"].queryset.count(), 0)  # sees none of restaurant A's dishes
        self.assertEqual(self.signup(Client(), restaurant="Karachi Grill", username="hina2").status_code, 302)
        self.assertEqual(Restaurant.objects.get(name="Karachi Grill", slug="karachi-grill-2").tables.count(), 5)

    def test_signup_rejects_bad_input_and_saves_nothing(self):
        for over, msg in [({"restaurant": ""}, "Enter your restaurant"), ({"tables": "0"}, "1 to 40"), ({"tables": "x"}, "how many"),
                          ({"password": "123"}, "too short"), ({"username": self.users["owner"].username}, "taken"), ({"email": "nope"}, "email")]:
            r = self.signup(**over)
            self.assertEqual(r.status_code, 400, over)
            self.assertContains(r, msg, status_code=400)
        self.assertFalse(Restaurant.objects.filter(name="Karachi Grill").exists())
        self.assertFalse(User.objects.filter(username="hina").exists())
        self.assertEqual(self.signup(website="spam").status_code, 400)  # bot trap

    def test_signups_are_rate_limited_per_address(self):
        for i in range(5):
            self.assertEqual(self.signup(username=f"own{i}", restaurant=f"R{i}").status_code, 302)
        self.assertContains(self.signup(username="own9", restaurant="R9"), "Too many sign-ups", status_code=400)

    def test_expired_or_paused_restaurant_stops_guest_orders_and_bookings(self):
        line = [{"dish_id": self.naan.id, "quantity": 1}]
        self.r.paid_until, self.r.trial_ends = date.today() - timedelta(days=1), None
        self.r.save()
        self.assertEqual(self.r.status, "expired")
        with self.assertRaises(services.OrderError):
            services.place_order(self.table, line)
        with self.assertRaises(services.OrderError):
            services.book_table(self.r, "Ali", "0300 1234567", 2, (timezone_now() + timedelta(days=1)).isoformat())
        services.record_plan_payment(self.r, "starter", 2, 9998, "IBFT-1", "platform")
        self.r.refresh_from_db()
        self.assertEqual((self.r.status, self.r.plan, self.r.paid_until), ("active", "starter", date.today() + timedelta(days=60)))
        services.place_order(self.table, line)
        services.record_plan_payment(self.r, "starter", 1, 4999)  # a second payment extends from the end, not from today
        self.r.refresh_from_db()
        self.assertEqual(self.r.paid_until, date.today() + timedelta(days=90))
        services.set_suspended(self.r, True)
        self.assertEqual(Restaurant.objects.get(pk=self.r.pk).status, "suspended")
        with self.assertRaises(services.OrderError):
            services.place_order(Table.objects.get(pk=self.table.pk), line)

    def test_plan_limits_on_tables_and_staff(self):
        services.set_plan(self.r, "starter")  # 10 tables, 5 staff
        services.add_tables(self.r, 9)
        with self.assertRaises(services.OrderError):
            services.add_tables(self.r, 1)
        services.set_access(self.users["owner"], "owner")
        owner = Client()
        owner.force_login(self.users["owner"])
        self.assertFalse(owner.get("/admin/core/table/").context["has_add_permission"])
        self.assertEqual(owner.get("/admin/core/table/add/").status_code, 403)
        services.add_staff(self.r, "waiter2", "", "waiter", "Str0ng-pass")
        services.add_staff(self.r, "waiter3", "", "waiter", "Str0ng-pass")
        r = self.post(owner, "/owner/team/data/", {"username": "waiter4", "role": "waiter", "password": "Str0ng-pass"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("allows 5 active staff", r.json()["error"])
        services.set_plan(self.r, "enterprise")
        self.assertEqual(self.post(owner, "/owner/team/data/", {"username": "waiter4", "role": "waiter", "password": "Str0ng-pass"}).status_code, 200)
        services.add_tables(self.r, 5)

    def test_platform_console_is_only_for_the_platform(self):
        boss = User.objects.create_superuser("platform", password="Platform-pass-1")
        c = Client()
        c.force_login(boss)
        self.assertEqual(c.get("/").url, "/platform/")
        data = c.get("/platform/data/").json()
        row = next(x for x in data["restaurants"] if x["id"] == self.r.id)
        self.assertEqual((row["status"], row["tables"], row["staff"]), ("active", 1, 3))
        self.assertEqual(self.post(c, f"/platform/restaurants/{self.r.id}/pay/", {"plan": "enterprise", "months": 3, "amount": 74997, "reference": "TX9"}).status_code, 200)
        self.assertEqual(self.post(c, f"/platform/restaurants/{self.r.id}/suspend/").status_code, 200)
        self.assertEqual(Restaurant.objects.get(pk=self.r.pk).status, "suspended")
        self.assertEqual(self.post(c, f"/platform/restaurants/{self.r.id}/resume/").status_code, 200)
        self.assertEqual(self.post(c, f"/platform/restaurants/{self.r.id}/pay/", {"months": 0, "amount": 1}).status_code, 400)
        data = c.get("/platform/data/").json()
        self.assertEqual((data["payments"][0]["amount"], data["payments"][0]["reference"], data["mrr"]), ("74997.00", "TX9", 24999))
        for user in (self.users["owner"], self.users["waiter"]):
            other = Client()
            other.force_login(user)
            self.assertEqual(other.get("/platform/").status_code, 403)
            self.assertEqual(self.post(other, f"/platform/restaurants/{self.r.id}/suspend/").status_code, 403)
        self.assertEqual(Client().get("/platform/data/").status_code, 302)

    def test_owner_sets_the_brand_and_guests_see_it(self):
        owner = Client()
        owner.force_login(self.users["owner"])
        form = {"name": "Grill A", "tagline": "Fire and smoke", "brand_color": "#e4572e", "currency": "Rs", "service_percent": "5", "tax_percent": "16",
                "wifi_name": "GrillA", "wifi_password": "x", "reward_rate": "10", "reward_goal": "200", "reward_text": "Free tea"}
        self.assertContains(owner.post("/owner/settings/", form), "Saved")
        page = Client().get(f"/t/{self.table.code}/")
        self.assertContains(page, "--acc: #e4572e")
        self.assertContains(page, "Grill A")
        self.assertContains(owner.post("/owner/settings/", dict(form, brand_color="#zz;}{x")), "Use a colour like")
        self.assertContains(owner.post("/owner/settings/", dict(form, brand_color="red;}body{display:none")), "at most 7")
        self.assertEqual(Restaurant.objects.get(pk=self.r.pk).brand_color, "#e4572e")
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        self.assertEqual(waiter.get("/owner/settings/").status_code, 403)

    def test_booking_flow_from_guest_to_staff(self):
        when = (timezone_now() + timedelta(days=2)).replace(hour=20, minute=0)
        r = Client().post(f"/book/{self.r.slug}/", {"name": "Ali", "phone": "0300 1234567", "email": "ali@mail.pk", "party": "4",
                                                    "date": when.strftime("%Y-%m-%d"), "time": "20:00", "note": "Birthday"})
        self.assertContains(r, "Thanks, Ali.")
        for bad in ({"name": "A"}, {"party": "0"}, {"date": "2000-01-01"}, {"phone": "12"}):
            data = {"name": "Ali", "phone": "0300 1234567", "party": "2", "date": when.strftime("%Y-%m-%d"), "time": "20:00"}
            data.update(bad)
            self.assertEqual(Client().post(f"/book/{self.r.slug}/", data).status_code, 400, bad)
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        rows = waiter.get("/staff/reservations/data/").json()["reservations"]
        self.assertEqual([(x["name"], x["party"], x["status"], x["note"]) for x in rows], [("Ali", 4, "requested", "Birthday")])
        rid = rows[0]["id"]
        self.assertEqual(self.post(waiter, f"/staff/reservations/{rid}/confirm/").status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("is confirmed", mail.outbox[0].subject)
        self.assertEqual(self.post(waiter, f"/staff/reservations/{rid}/confirm/").status_code, 400)
        other_table = make_restaurant("B")[1]
        self.assertEqual(self.post(waiter, f"/staff/reservations/{rid}/seat/", {"table_id": other_table.id}).status_code, 404)
        self.assertEqual(self.post(waiter, f"/staff/reservations/{rid}/seat/", {"table_id": self.table.id}).status_code, 200)
        self.assertEqual(waiter.get("/staff/reservations/data/").json()["reservations"][0]["table"], 1)
        kitchen = Client()
        kitchen.force_login(self.users["kitchen"])
        self.assertEqual(kitchen.get("/staff/reservations/data/").status_code, 403)
        self.assertEqual(Client().get("/book/nope/").status_code, 404)


def timezone_now():
    from django.utils import timezone
    return timezone.now()


class Inventory(TestCase):
    def setUp(self):
        from .models import Ingredient, RecipeItem
        self.r, self.table, self.curry, self.naan, self.users = make_restaurant("A")
        self.paneer = Ingredient.objects.create(restaurant=self.r, name="Paneer", unit="g", stock=500, low_at=200)
        self.dough = Ingredient.objects.create(restaurant=self.r, name="Dough", unit="g", stock=1000, low_at=100)
        RecipeItem.objects.create(dish=self.curry, ingredient=self.paneer, amount=150)  # per portion
        RecipeItem.objects.create(dish=self.naan, ingredient=self.dough, amount=100)
        self.half = self.curry.sizes.get(label="Half")

    def stock(self, ing):
        ing.refresh_from_db()
        return ing.stock

    def test_sending_to_the_kitchen_takes_stock_and_cancelling_gives_it_back(self):
        order = services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": self.half.id, "quantity": 2}, {"dish_id": self.naan.id, "quantity": 3}])
        self.assertEqual((self.stock(self.paneer), self.stock(self.dough)), (500, 1000))  # placing alone takes nothing
        services.advance_order(order, "waiter")
        self.assertEqual((self.stock(self.paneer), self.stock(self.dough)), (200, 700))
        services.remove_item(order.items.get(dish=self.naan), "waiter")
        self.assertEqual(self.stock(self.dough), 1000)
        services.cancel_order(order, "waiter")
        self.assertEqual(self.stock(self.paneer), 500)

    def test_dish_sells_out_when_an_ingredient_runs_out_and_returns_on_restock(self):
        from .models import Dish
        order = services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": self.half.id, "quantity": 3}])
        services.advance_order(order, "waiter")  # 500 - 450 = 50 g left, less than one portion
        curry = Dish.objects.get(pk=self.curry.pk)
        self.assertEqual((curry.available, curry.auto_out), (False, True))
        self.assertEqual(services.low_stock(self.r)[0]["name"], "Paneer")
        with self.assertRaises(services.OrderError):
            services.place_order(self.table, [{"dish_id": self.curry.id, "size_id": self.half.id, "quantity": 1}])
        services.adjust_stock(self.paneer, add=1000)
        self.assertTrue(Dish.objects.get(pk=self.curry.pk).available)
        # a dish a person marked sold out stays sold out after a restock
        services.set_available(Dish.objects.get(pk=self.curry.pk), False)
        services.adjust_stock(self.paneer, add=10)
        self.assertFalse(Dish.objects.get(pk=self.curry.pk).available)

    def test_owner_inventory_page_adds_counts_and_rejects_nonsense(self):
        owner = Client()
        owner.force_login(self.users["owner"])
        post = lambda path, d: owner.post(path, json.dumps(d), content_type="application/json")
        r = post("/owner/inventory/data/", {"name": "Lassi mix", "unit": "ml", "stock": "2000", "low_at": "500"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Lassi mix", [i["name"] for i in r.json()["ingredients"]])
        self.assertEqual(post("/owner/inventory/data/", {"name": "paneer", "unit": "g"}).status_code, 400)  # duplicate
        self.assertEqual(post("/owner/inventory/data/", {"name": "Salt", "unit": "kg"}).status_code, 400)
        self.assertEqual(post(f"/owner/inventory/{self.paneer.id}/", {"count": "320.5", "low_at": "100"}).status_code, 200)
        self.assertEqual(self.stock(self.paneer), Decimal("320.50"))
        for bad in ({"add": "lots"}, {"count": "NaN"}, {"add": "1e30"}):
            self.assertEqual(post(f"/owner/inventory/{self.paneer.id}/", bad).status_code, 400, bad)
        row = next(i for i in owner.get("/owner/inventory/data/").json()["ingredients"] if i["name"] == "Paneer")
        self.assertEqual((row["low"], row["dishes"][0]["portions"]), (False, 2))
        other = make_restaurant("B")[0]
        from .models import Ingredient
        theirs = Ingredient.objects.create(restaurant=other, name="Rice", stock=1)
        self.assertEqual(post(f"/owner/inventory/{theirs.id}/", {"add": "5"}).status_code, 404)
        waiter = Client()
        waiter.force_login(self.users["waiter"])
        self.assertEqual(waiter.get("/owner/inventory/data/").status_code, 403)
        kitchen = Client()
        kitchen.force_login(self.users["kitchen"])
        services.adjust_stock(self.paneer, count=50)
        self.assertEqual([x["name"] for x in kitchen.get("/kitchen/data/").json()["low"]], ["Paneer"])


class HealthCheck(TestCase):
    def test_health_check_answers_any_host_without_redirect(self):
        from django.test import override_settings
        with override_settings(ALLOWED_HOSTS=["example.com"], SECURE_SSL_REDIRECT=True):
            r = Client().get("/healthz/", HTTP_HOST="10.0.0.7:10000")
        self.assertEqual((r.status_code, r.content), (200, b"ok"))
