"""Starter categories created for every new user."""

# (name, flow, is_essential, [subcategories])
DEFAULT_CATEGORIES = [
    ("Rent & housing", "expense", True, []),
    ("Groceries", "expense", True, []),
    ("Food & dining", "expense", False, []),
    ("Transport", "expense", True, []),
    ("Utilities & bills", "expense", True, []),
    ("Education", "expense", True, []),
    ("Health", "expense", True, []),
    ("EMI & loans", "expense", True, []),
    ("Subscriptions", "expense", False, []),
    ("Entertainment", "expense", False, []),
    ("Shopping", "expense", False, []),
    ("Family", "expense", True, []),
    ("Other", "expense", False, []),
    ("Salary", "income", False, []),
    ("Other income", "income", False, []),
]

# Names used by the pre-ledger app's fixed choices -> new category names.
LEGACY_CATEGORY_MAP = {
    "food": "Food & dining",
    "transport": "Transport",
    "bills": "Utilities & bills",
    "entertainment": "Entertainment",
    "other": "Other",
}
