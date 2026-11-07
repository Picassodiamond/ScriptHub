from django.urls import path

from . import execution_views, script_views, views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("scripts/", views.library, name="library"),
    path("scripts/refresh/", views.library_refresh, name="library_refresh"),
    path("scripts/<str:script_id>/toggle/", views.script_toggle, name="script_toggle"),
    path("scripts/<str:script_id>/", script_views.script_detail, name="script_detail"),
    path("scripts/<str:script_id>/configure/", script_views.script_configure, name="script_configure"),
    path("scripts/<str:script_id>/configure/raw/", script_views.script_configure_raw, name="script_configure_raw"),
    path("scripts/<str:script_id>/source/", script_views.script_source, name="script_source"),
    path("scripts/<str:script_id>/run/", execution_views.script_run, name="script_run"),
    path("scripts/<str:script_id>/start/", execution_views.script_start, name="script_start"),
    path("executions/", execution_views.executions_list, name="executions"),
    path("executions/<str:run_id>/", execution_views.execution_console, name="execution_console"),
    path("executions/<str:run_id>/status/", execution_views.execution_status, name="execution_status"),
    path("executions/<str:run_id>/stop/", execution_views.execution_stop, name="execution_stop"),
    path("executions/<str:run_id>/log/<str:stream>/", execution_views.execution_log, name="execution_log"),
    path("settings/hosts/test/", views.settings_test_host, name="settings_test_host"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/directories/add/", views.settings_add_directory, name="settings_add_directory"),
    path("settings/directories/remove/", views.settings_remove_directory, name="settings_remove_directory"),
]
