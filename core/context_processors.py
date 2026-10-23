from .models import Notification

NAV = [
    (
        "Plan",
        [
            ("dashboard", "Overview"),
            ("cashflow", "Cash flow"),
            ("budget_list", "Budgets"),
            ("bill_list", "Bills & income"),
            ("goal_list", "Goals"),
            ("debt_list", "Debt"),
            ("purchase", "Can I afford it?"),
        ],
    ),
    (
        "Records",
        [
            ("transaction_list", "Transactions"),
            ("account_list", "Accounts"),
            ("category_list", "Categories"),
            ("import_upload", "Import CSV"),
        ],
    ),
    ("Insight", [("reports", "Reports"), ("calendar", "Calendar")]),
]


def shell(request):
    """Navigation state and the unread-notification count for the app shell."""
    if not request.user.is_authenticated:
        return {}
    match = request.resolver_match
    return {
        "nav_groups": NAV,
        "current_url_name": match.url_name if match else "",
        "unread_count": Notification.objects.filter(user=request.user, read_at__isnull=True).count(),
    }
