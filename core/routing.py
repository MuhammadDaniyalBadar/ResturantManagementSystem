from django.urls import path

from .consumers import GuestConsumer, StaffConsumer

websocket_urlpatterns = [
    path("ws/staff/", StaffConsumer.as_asgi()),
    path("ws/t/<str:code>/", GuestConsumer.as_asgi()),
]
