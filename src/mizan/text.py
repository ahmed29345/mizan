"""Small text helpers shared by the agent and the evaluations."""

# Arabic-Indic (٠-٩) and Persian (۰-۹) digits -> 0-9, so "٢٤ ساعة" matches "24".
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise(text: str) -> str:
    """Western digits, lower case, single spaces: for comparing text, never for display."""
    return " ".join(text.translate(_DIGITS).lower().split())
