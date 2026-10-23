from django.urls import path

from . import views

urlpatterns = [
    path("cash-flow/", views.cashflow, name="cashflow"),
    path("budgets/", views.budget_list, name="budget_list"),
    path("budgets/add/", views.budget_create, name="budget_create"),
    path("budgets/<int:pk>/edit/", views.budget_update, name="budget_update"),
    path("budgets/<int:pk>/delete/", views.budget_delete, name="budget_delete"),
    path("bills/", views.bill_list, name="bill_list"),
    path("bills/add/", views.bill_create, name="bill_create"),
    path("bills/income/add/", views.income_create, name="income_create"),
    path("bills/record/", views.schedule_record, name="schedule_record"),
    path("bills/<int:pk>/edit/", views.schedule_update, name="schedule_update"),
    path("bills/<int:pk>/delete/", views.schedule_delete, name="schedule_delete"),
    path("debt/", views.debt_list, name="debt_list"),
    path("debt/add/", views.debt_create, name="debt_create"),
    path("debt/compare/", views.debt_compare, name="debt_compare"),
    path("debt/<int:pk>/", views.debt_detail, name="debt_detail"),
    path("debt/<int:pk>/edit/", views.debt_update, name="debt_update"),
    path("debt/<int:pk>/delete/", views.debt_delete, name="debt_delete"),
    path("goals/", views.goal_list, name="goal_list"),
    path("goals/add/", views.goal_create, name="goal_create"),
    path("goals/<int:pk>/edit/", views.goal_update, name="goal_update"),
    path("goals/<int:pk>/delete/", views.goal_delete, name="goal_delete"),
    path("goals/<int:pk>/contribute/", views.goal_contribute, name="goal_contribute"),
    path("can-i-afford-it/", views.purchase, name="purchase"),
    path("reports/", views.reports, name="reports"),
    path("reports/export/", views.reports_export, name="reports_export"),
    path("calendar/", views.calendar_view, name="calendar"),
]
