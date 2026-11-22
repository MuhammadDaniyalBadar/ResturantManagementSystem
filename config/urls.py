from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from core.views import Login, healthz

urlpatterns = [
    path("healthz/", healthz),
    path("accounts/login/", Login.as_view(), name="login"),
    path("admin/login/", Login.as_view()),
    path("admin/", admin.site.urls),
    # shortcut: Django serves uploaded photos itself, fine for one restaurant's menu; move to S3 or a CDN at scale.
    re_path(r"^media/(?P<path>.*)$", lambda request, path: serve(request, path, document_root=settings.MEDIA_ROOT)),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("core.urls")),
]
