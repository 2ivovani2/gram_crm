"""
Manager documentation views.
Access: any staff (is_staff) or superuser.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View


@method_decorator(staff_member_required, name="dispatch")
class DocsIndexView(View):
    def get(self, request):
        return render(request, "docs/index.html")


@method_decorator(staff_member_required, name="dispatch")
class DocsCRMView(View):
    def get(self, request):
        return render(request, "docs/crm.html")


@method_decorator(staff_member_required, name="dispatch")
class DocsSpamControlView(View):
    def get(self, request):
        return render(request, "docs/spamcontrol.html")


@method_decorator(staff_member_required, name="dispatch")
class DocsRatesView(View):
    def get(self, request):
        return render(request, "docs/rates.html")


@method_decorator(staff_member_required, name="dispatch")
class DocsFAQView(View):
    def get(self, request):
        return render(request, "docs/faq.html")
