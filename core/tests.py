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
