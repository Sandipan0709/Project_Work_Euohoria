from django.contrib import admin

from .models import Account, Category, ImportBatch, Transaction

admin.site.register(Account)
admin.site.register(Category)
admin.site.register(ImportBatch)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "kind", "amount", "account", "payee", "user")
    list_filter = ("kind",)
