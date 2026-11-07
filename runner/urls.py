from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="library", permanent=False)),
    path("scripts/", views.library, name="library"),
    path("scripts/refresh/", views.library_refresh, name="library_refresh"),
    path("scripts/<str:script_id>/toggle/", views.script_toggle, name="script_toggle"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/directories/add/", views.settings_add_directory, name="settings_add_directory"),
    path("settings/directories/remove/", views.settings_remove_directory, name="settings_remove_directory"),
]
