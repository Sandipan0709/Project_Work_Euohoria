import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Notification
from core.views_util import delete_page, form_page, safe_next
from planner.services import delete_transaction

from . import importing
from .forms import (
    AccountForm,
    CategoryForm,
    ImportMapForm,
    ImportUploadForm,
    TransactionFilterForm,
    TransactionForm,
)
from .models import Account, Category, ImportBatch, Transaction
from .services import account_balances

K = Transaction.Kind
PAGE_SIZE = 25


def _filtered(request):
    form = TransactionFilterForm(request.GET or None, user=request.user)
    qs = Transaction.objects.filter(user=request.user).select_related("account", "to_account", "category")
    sort = "-date"
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            qs = qs.filter(Q(payee__icontains=d["q"]) | Q(notes__icontains=d["q"]) | Q(tags__icontains=d["q"]))
        if d["kind"]:
            qs = qs.filter(kind=d["kind"])
        if d["account"]:
            qs = qs.filter(Q(account=d["account"]) | Q(to_account=d["account"]))
        if d["category"]:
            qs = qs.filter(Q(category=d["category"]) | Q(category__parent=d["category"]))
        if d["date_from"]:
            qs = qs.filter(date__gte=d["date_from"])
        if d["date_to"]:
            qs = qs.filter(date__lte=d["date_to"])
        sort = d["sort"] or "-date"
    if request.GET.get("uncategorised"):
        qs = qs.filter(category__isnull=True, kind=K.EXPENSE)
    return form, qs.order_by(sort, "-id")


@login_required
def transaction_list(request):
    form, qs = _filtered(request)

    def total(kind):
        return qs.filter(kind=kind).aggregate(t=Sum("amount"))["t"] or 0

    summary = {"income": total(K.INCOME), "spending": total(K.EXPENSE) - total(K.REFUND), "count": qs.count()}
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    categories = Category.objects.filter(user=request.user).select_related("parent")
    return render(
        request,
        "ledger/transaction_list.html",
        {
            "form": form,
            "page": page,
            "summary": summary,
            "categories": categories,
            "has_accounts": Account.objects.filter(user=request.user).exists(),
            "filtered": any(v for k, v in request.GET.items() if k not in ("page", "sort")),
        },
    )


@login_required
def transaction_create(request):
    kind = request.GET.get("kind")
    initial = {"date": timezone.localdate(), "kind": kind if kind in K.values else K.EXPENSE}
    return form_page(
        request,
        TransactionForm,
        title="Add transaction",
        back="transaction_list",
        success_message="Transaction added.",
        initial=initial,
        template="ledger/transaction_form.html",
    )


@login_required
def transaction_update(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, user=request.user)
    return form_page(
        request,
        TransactionForm,
        instance=txn,
        title="Edit transaction",
        back="transaction_list",
        success_message="Transaction updated.",
        template="ledger/transaction_form.html",
    )


@login_required
def transaction_delete(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, user=request.user)
    return delete_page(
        request,
        txn,
        title="Delete this transaction?",
        back="transaction_list",
        warning=(
            "If it recorded a bill payment or debt payment, that bill becomes due again "
            "and the debt balance is restored."
        ),
        success_message="Transaction deleted.",
        delete=delete_transaction,
    )


@login_required
@require_POST
def transaction_bulk(request):
    ids = [i for i in request.POST.getlist("ids") if i.isdigit()]
    selected = Transaction.objects.filter(user=request.user, pk__in=ids)
    back = safe_next(request, "")
    action = request.POST.get("action")
    if not ids or not selected.exists():
        messages.error(request, "Select at least one transaction first.")
        return redirect(back or "transaction_list")
    if action == "categorise":
        category = Category.objects.filter(user=request.user, pk=request.POST.get("category") or 0).first()
        if not category:
            messages.error(request, "Choose a category to apply.")
        else:
            count = selected.exclude(kind=K.TRANSFER).update(category=category)
            messages.success(request, f"Set the category of {count} transaction(s) to {category}.")
    elif action == "delete":
        if request.POST.get("confirmed") != "yes":
            return render(request, "ledger/bulk_confirm.html", {"count": selected.count(), "ids": ids, "next": back})
        with transaction.atomic():
            for txn in selected:
                delete_transaction(txn)
        messages.success(request, f"Deleted {len(ids)} transaction(s).")
    else:
        messages.error(request, "Choose an action.")
    return redirect(back or "transaction_list")


@login_required
def export_csv(request):
    _, qs = _filtered(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="fintrackr-transactions-{timezone.localdate()}.csv"'
    writer = csv.writer(response)
    writer.writerow(importing.EXPORT_HEADER)
    for txn in qs.iterator():
        writer.writerow(importing.export_row(txn))
    return response


# ---- accounts -------------------------------------------------------------------------


@login_required
def account_list(request):
    balances = account_balances(request.user)
    accounts = Account.objects.filter(user=request.user)
    rows = [(a, balances[a.id]) for a in accounts]
    return render(
        request,
        "ledger/account_list.html",
        {
            "active": [r for r in rows if not r[0].is_archived],
            "archived": [r for r in rows if r[0].is_archived],
        },
    )


@login_required
def account_create(request):
    return form_page(
        request,
        AccountForm,
        title="Add account",
        back="account_list",
        success_message="Account added.",
        intro="Each account keeps its own balance. Transfers between accounts are not counted as income or spending.",
    )


@login_required
def account_update(request, pk):
    account = get_object_or_404(Account, pk=pk, user=request.user)
    return form_page(
        request,
        AccountForm,
        instance=account,
        title="Edit account",
        back="account_list",
        success_message="Account updated.",
    )


@login_required
@require_POST
def account_archive(request, pk):
    account = get_object_or_404(Account, pk=pk, user=request.user)
    account.is_archived = not account.is_archived
    account.save(update_fields=["is_archived"])
    messages.success(request, f"{account.name} {'archived' if account.is_archived else 'restored'}.")
    return redirect("account_list")


@login_required
def account_delete(request, pk):
    account = get_object_or_404(Account, pk=pk, user=request.user)
    if account.transactions.exists() or account.incoming_transfers.exists():
        messages.error(request, "This account has transactions, so it cannot be deleted. Archive it instead.")
        return redirect("account_list")
    return delete_page(
        request,
        account,
        title=f"Delete {account.name}?",
        back="account_list",
        warning="The account has no transactions. Bills and debts that used it will have no account set.",
        success_message="Account deleted.",
    )


# ---- categories -----------------------------------------------------------------------


@login_required
def category_list(request):
    cats = list(Category.objects.filter(user=request.user).select_related("parent"))
    tops = [c for c in cats if c.parent_id is None]
    tree = [(c, [k for k in cats if k.parent_id == c.id]) for c in tops]
    return render(request, "ledger/category_list.html", {"tree": tree})


@login_required
def category_create(request):
    return form_page(
        request, CategoryForm, title="Add category", back="category_list", success_message="Category added."
    )


@login_required
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk, user=request.user)
    return form_page(
        request,
        CategoryForm,
        instance=category,
        title="Edit category",
        back="category_list",
        success_message="Category updated.",
    )


@login_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk, user=request.user)
    return delete_page(
        request,
        category,
        title=f"Delete {category}?",
        back="category_list",
        warning="Transactions in it become uncategorised. Its subcategories and any budgets set on it are deleted too.",
        success_message="Category deleted.",
    )


# ---- CSV import -----------------------------------------------------------------------

SESSION_KEY = "csv_import"


@login_required
def import_upload(request):
    form = ImportUploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            text = importing.decode(upload.read())
            importing.read_table(text)
        except importing.ImportError_ as exc:
            form.add_error("file", str(exc))
        else:
            request.session[SESSION_KEY] = {
                "text": text,
                "filename": upload.name,
                "account_id": form.cleaned_data["account"].pk,
            }
            return redirect("import_map")
    has_accounts = Account.objects.filter(user=request.user, is_archived=False).exists()
    return render(request, "ledger/import_upload.html", {"form": form, "has_accounts": has_accounts})


@login_required
def import_map(request):
    data = request.session.get(SESSION_KEY)
    if not data:
        return redirect("import_upload")
    account = get_object_or_404(Account, pk=data["account_id"], user=request.user)
    headers, rows = importing.read_table(data["text"])
    form = ImportMapForm(
        request.POST or None, headers=headers, initial={**importing.guess_mapping(headers), "date_format": "dmy"}
    )
    report = None
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        mapping = importing.Mapping(
            date=c["date"],
            amount=c["amount"],
            debit=c["debit"],
            credit=c["credit"],
            payee=c["payee"],
            category=c["category"],
            notes=c["notes"],
            date_format=c["date_format"],
            amount_mode=c["amount_mode"],
        )
        try:
            report = importing.build_report(request.user, account, rows, mapping)
        except importing.ImportError_ as exc:
            form.add_error(None, str(exc))
        if report and "confirm" in request.POST:
            if not report.counts["ok"]:
                messages.error(request, "There are no new rows to import.")
            else:
                try:
                    batch = importing.commit_import(request.user, account, data["filename"], report)
                except IntegrityError:
                    messages.error(
                        request, "Some of these rows were imported by another request. Review the preview again."
                    )
                else:
                    Notification.objects.get_or_create(
                        user=request.user,
                        key=f"import:{batch.pk}",
                        defaults={
                            "kind": Notification.Kind.IMPORT,
                            "link_name": "import_history",
                            "message": f"Imported {batch.rows_imported} transaction(s) from {batch.filename} "
                            f"({batch.rows_duplicate} duplicate, {batch.rows_invalid} invalid).",
                        },
                    )
                    request.session.pop(SESSION_KEY, None)
                    messages.success(request, f"Imported {batch.rows_imported} transaction(s).")
                    return redirect("import_history")
    return render(
        request,
        "ledger/import_map.html",
        {
            "form": form,
            "account": account,
            "filename": data["filename"],
            "row_count": len(rows),
            "report": report,
            "preview_ok": report.of("ok")[:15] if report else [],
            "invalid": report.of("invalid")[:50] if report else [],
            "duplicates": report.of("duplicate")[:20] if report else [],
        },
    )


@login_required
@require_POST
def import_cancel(request):
    request.session.pop(SESSION_KEY, None)
    return redirect("import_upload")


@login_required
def import_history(request):
    batches = ImportBatch.objects.filter(user=request.user).select_related("account")[:50]
    return render(request, "ledger/import_history.html", {"batches": batches})
