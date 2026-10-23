"""Small helpers for building test data."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User

from ledger.models import Account, Category, Transaction


def make_user(username="alice", password="a-long-test-password-1"):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password=password)


def make_account(user, name="HDFC", kind=Account.Kind.BANK, opening="0"):
    return Account.objects.create(user=user, name=name, kind=kind, opening_balance=Decimal(opening))


def make_txn(user, account, kind, amount, day=None, to_account=None, category=None, payee="", **extra):
    return Transaction.objects.create(
        user=user,
        account=account,
        kind=kind,
        amount=Decimal(str(amount)),
        date=day or date(2025, 10, 1),
        to_account=to_account,
        category=category,
        payee=payee,
        **extra,
    )


def category(user, name):
    return Category.objects.get(user=user, name=name)
