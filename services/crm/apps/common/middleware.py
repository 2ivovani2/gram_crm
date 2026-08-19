from django.conf import settings
from django.shortcuts import render


PRIVATE_HOSTS = {
    "crm.gramly.tech": ("Gramly CRM", "бизнес-пространство"),
    "git.gramly.tech": ("Gramly Git", "репозитории и pull request"),
    "tasks.gramly.tech": ("Gramly Tasks", "задачи и командные проекты"),
    "docs.gramly.tech": ("Gramly Docs", "внутренняя база знаний"),
}

PUBLIC_HOSTS = {"gramly.tech", "www.gramly.tech", "hello.gramly.tech"}
PUBLIC_PREFIXES = (
    "/static/",
    "/health/",
    "/bot/webhook/",
)


class PrivateAccessGateMiddleware:
    """Render the public VPN gate before private application routes resolve."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.PUBLIC_ACCESS_GATE:
            # Use the raw WSGI/ASGI value so this guard runs before any code
            # calls request.get_host(). Only exact, static hostnames are used.
            host = request.META.get("HTTP_HOST", "").split(":", 1)[0].lower()
            service = PRIVATE_HOSTS.get(host)
            if service:
                return render(
                    request,
                    "private_access_required.html",
                    {"service_name": service[0], "service_description": service[1]},
                    status=403,
                )
            if host in PUBLIC_HOSTS:
                is_public_path = request.path == "/" or request.path.startswith(PUBLIC_PREFIXES)
                if not is_public_path:
                    return render(request, "404.html", status=404)
        return self.get_response(request)
