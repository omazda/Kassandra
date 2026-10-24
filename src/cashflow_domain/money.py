from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_minor(value: str) -> int:
    try:
        amount = Decimal(value)
        cents = amount * 100
        if not amount.is_finite() or cents != cents.to_integral_value() or abs(cents) > 10**16:
            raise ValueError("Сумма должна содержать не более двух десятичных знаков")
        return int(cents)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Некорректная денежная сумма") from exc


def model_minor(value: float) -> int:
    """One explicit rounding boundary between the ML float and monetary engine."""
    return max(0, int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
