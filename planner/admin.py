from django.contrib import admin

from .models import Budget, Debt, DebtPayment, Goal, GoalContribution, Occurrence, ScheduledItem

for model in (Budget, Debt, DebtPayment, Goal, GoalContribution, Occurrence, ScheduledItem):
    admin.site.register(model)
