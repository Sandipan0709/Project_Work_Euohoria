from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="core/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("overview/", views.dashboard, name="dashboard"),
    path("welcome/", views.onboarding, name="onboarding"),
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/read/", views.notification_read, name="notification_read"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/export/", views.export_data, name="export_data"),
    path("settings/delete/", views.delete_account, name="delete_account"),
    # Links from the pre-ledger app keep working.
    path("expenses/", RedirectView.as_view(pattern_name="transaction_list"), name="expense_list"),
    path("budget/", RedirectView.as_view(pattern_name="budget_list"), name="budget_view"),
]
