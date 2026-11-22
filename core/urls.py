from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("book/<slug:slug>/", views.book, name="book"),
    path("platform/", views.platform_page, name="platform"),
    path("platform/data/", views.platform_data, name="platform_data"),
    path("platform/restaurants/<int:pk>/<str:action>/", views.platform_action, name="platform_action"),
    # guest
    path("t/<str:code>/", views.table_page, name="table"),
    path("t/<str:code>/state/", views.table_state, name="table_state"),
    path("t/<str:code>/join/", views.table_join, name="table_join"),
    path("t/<str:code>/member/", views.table_member, name="table_member"),
    path("t/<str:code>/order/", views.table_order, name="table_order"),
    path("t/<str:code>/orders/<int:pk>/cancel/", views.table_cancel, name="table_cancel"),
    path("t/<str:code>/call/", views.table_call, name="table_call"),
    path("t/<str:code>/pay/", views.table_pay, name="table_pay"),
    path("t/<str:code>/review/", views.table_review, name="table_review"),
    path("t/<str:code>/quiz/", views.table_quiz, name="table_quiz"),
    path("t/<str:code>/quiz/answer/", views.table_quiz_answer, name="table_quiz_answer"),
    # staff
    path("staff/", views.staff_page, name="staff"),
    path("staff/data/", views.staff_data, name="staff_data"),
    path("staff/orders/<int:pk>/advance/", views.order_advance, name="order_advance"),
    path("staff/orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("staff/items/<int:pk>/remove/", views.item_remove, name="item_remove"),
    path("staff/dishes/<int:pk>/available/", views.dish_available, name="dish_available"),
    path("staff/requests/<int:pk>/<str:action>/", views.request_action, name="request_action"),
    path("staff/payments/<int:pk>/paid/", views.payment_paid, name="payment_paid"),
    path("staff/bills/<int:pk>/close/", views.bill_close, name="bill_close"),
    path("staff/qr/", views.qr_sheet, name="qr"),
    path("staff/reservations/", views.reservations_page, name="reservations"),
    path("staff/reservations/data/", views.reservations_data, name="reservations_data"),
    path("staff/reservations/<int:pk>/<str:action>/", views.reservation_action, name="reservation_action"),
    # kitchen
    path("kitchen/", views.kitchen_page, name="kitchen"),
    path("kitchen/data/", views.kitchen_data, name="kitchen_data"),
    path("kitchen/orders/<int:pk>/ready/", views.kitchen_ready, name="kitchen_ready"),
    # owner
    path("owner/", views.owner_page, name="owner"),
    path("owner/data/", views.owner_data, name="owner_data"),
    path("owner/team/", views.team_page, name="team"),
    path("owner/settings/", views.settings_page, name="settings"),
    path("owner/team/data/", views.team_data, name="team_data"),
    path("owner/team/<int:pk>/", views.team_member, name="team_member"),
]
