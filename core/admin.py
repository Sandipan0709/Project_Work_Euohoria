from django.contrib import admin

from .models import Budget, Expense, Notification, Profile

admin.site.register(Profile)
admin.site.register(Budget)
admin.site.register(Expense)
admin.site.register(Notification)
