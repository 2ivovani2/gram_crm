from django.contrib import admin
from django.contrib.auth.models import Group

admin.site.site_header = "Gramly Admin"
admin.site.site_title = "Gramly"
admin.site.index_title = "Панель управления"

# Group is unused — auth is Telegram-based
admin.site.unregister(Group)
