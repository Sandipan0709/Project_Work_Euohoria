from django.urls import path

from . import views

urlpatterns = [
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/add/", views.transaction_create, name="transaction_create"),
    path("transactions/bulk/", views.transaction_bulk, name="transaction_bulk"),
    path("transactions/export/", views.export_csv, name="export_csv"),
    path("transactions/<int:pk>/edit/", views.transaction_update, name="transaction_update"),
    path("transactions/<int:pk>/delete/", views.transaction_delete, name="transaction_delete"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/add/", views.account_create, name="account_create"),
    path("accounts/<int:pk>/edit/", views.account_update, name="account_update"),
    path("accounts/<int:pk>/archive/", views.account_archive, name="account_archive"),
    path("accounts/<int:pk>/delete/", views.account_delete, name="account_delete"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/add/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("import/", views.import_upload, name="import_upload"),
    path("import/map/", views.import_map, name="import_map"),
    path("import/cancel/", views.import_cancel, name="import_cancel"),
    path("import/history/", views.import_history, name="import_history"),
]
