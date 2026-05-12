from django.contrib import admin

# Common admin configuration — kept minimal.
# The primary management interface is the Telegram admin panel.
admin.site.site_header = "SpamBotControl — Django Admin"
admin.site.site_title = "SpamBotControl"
admin.site.index_title = "Technical admin panel (superuser only)"
