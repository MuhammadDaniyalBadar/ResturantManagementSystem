from django.http import HttpResponse


def health_check(get_response):
    """Answer the host's health check before host checks and the HTTPS redirect: it calls by internal address over plain HTTP."""
    def middleware(request):
        if request.path == "/healthz/":
            return HttpResponse("ok", content_type="text/plain")
        return get_response(request)
    return middleware
