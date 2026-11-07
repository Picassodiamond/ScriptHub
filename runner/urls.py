from django.urls import path
from django.views.generic import RedirectView

from . import script_views, views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="library", permanent=False)),
    path("scripts/", views.library, name="library"),
    path("scripts/refresh/", views.library_refresh, name="library_refresh"),
    path("scripts/<str:script_id>/toggle/", views.script_toggle, name="script_toggle"),
    path("scripts/<str:script_id>/", script_views.script_detail, name="script_detail"),
    path("scripts/<str:script_id>/configure/", script_views.script_configure, name="script_configure"),
    path("scripts/<str:script_id>/configure/raw/", script_views.script_configure_raw, name="script_configure_raw"),
    path("scripts/<str:script_id>/run/", script_views.script_run, name="script_run"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/directories/add/", views.settings_add_directory, name="settings_add_directory"),
    path("settings/directories/remove/", views.settings_remove_directory, name="settings_remove_directory"),
]
