from django.urls import path

from . import views

app_name = "owners"

urlpatterns = [
    path("", views.OwnersDashboardView.as_view(), name="dashboard"),
    path("new/", views.OwnerCreateView.as_view(), name="create"),
    path("statuses/", views.OwnerStatusesView.as_view(), name="statuses"),
    path("statuses/<int:pk>/<str:action>/", views.OwnerStatusActionView.as_view(), name="status_action"),
    path("filters/<str:action>/", views.SavedFilterActionView.as_view(), name="filter_action"),
    path("filters/<str:action>/<int:pk>/", views.SavedFilterActionView.as_view(), name="filter_item_action"),
    path("<int:pk>/", views.OwnerDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.OwnerEditView.as_view(), name="edit"),
    path("<int:pk>/activity/", views.OwnerActivityView.as_view(), name="activity"),
    path("<int:pk>/action/<str:action>/", views.OwnerActionView.as_view(), name="action"),
    path("<int:pk>/channels/add/", views.OwnerChannelCreateView.as_view(), name="channel_add"),
    path(
        "<int:pk>/channels/<int:channel_pk>/<str:action>/",
        views.OwnerChannelActionView.as_view(), name="channel_action",
    ),
]
