from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    # guest
    path("t/<str:code>/", views.table_page, name="table"),
    path("t/<str:code>/state/", views.table_state, name="table_state"),
    path("t/<str:code>/order/", views.table_order, name="table_order"),
    path("t/<str:code>/call/", views.table_call, name="table_call"),
    # staff
    path("staff/", views.staff_page, name="staff"),
    path("staff/data/", views.staff_data, name="staff_data"),
    path("staff/orders/<int:pk>/advance/", views.order_advance, name="order_advance"),
    path("staff/requests/<int:pk>/<str:action>/", views.request_action, name="request_action"),
    path("staff/qr/", views.qr_sheet, name="qr"),
    # kitchen
    path("kitchen/", views.kitchen_page, name="kitchen"),
    path("kitchen/data/", views.kitchen_data, name="kitchen_data"),
    path("kitchen/orders/<int:pk>/ready/", views.kitchen_ready, name="kitchen_ready"),
]
