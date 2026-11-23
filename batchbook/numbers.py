"""Use integer paise for money and integer thousandths for kg and litres."""

from decimal import Decimal, DecimalException, InvalidOperation

from .db import BusinessError

UNIT_MAP = {
    "kg": {"kg": 1000, "g": 1},
    "L": {"L": 1000, "ml": 1},
    "piece": {"piece": 1},
    "packet": {"packet": 1},
}


def scaled(value, factor, label, *, minimum=0, maximum=100_000_000):
    try:
        amount = Decimal(str(value)) * factor
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise InvalidOperation
        if not minimum <= amount <= maximum:
            raise InvalidOperation
        return int(amount)
    except (DecimalException, TypeError, ValueError):
        raise BusinessError(f"{label} must be a valid amount in the selected unit.") from None


def round_ratio(numerator, denominator):
    """Round a rational integer half away from zero."""
    whole, rest = divmod(abs(numerator), denominator)
    sign = -1 if numerator < 0 else 1
    return sign * (whole + int(rest * 2 >= denominator))


def unit_scale(item):
    return UNIT_MAP[item["unit"]][item["unit"]]


def quantity(value, unit, item, *, allow_zero=False):
    units = UNIT_MAP[item["unit"]]
    factor = item["packets_per_carton"] if item["unit"] == "packet" and unit == "carton" else units.get(unit)
    if factor is None:
        raise BusinessError(f"{unit} is incompatible with {item['name']}.")
    if item["unit"] == "packet" and unit == "carton":
        return scaled(value, 1, "Cartons", minimum=int(not allow_zero), maximum=100_000_000 // factor) * factor
    return scaled(value, factor, item["name"], minimum=int(not allow_zero))


def qty_text(value, unit):
    if unit in {"kg", "L"}:
        return f"{Decimal(value) / 1000:.3f}".rstrip("0").rstrip(".")
    return str(value)


def amount_text(value):
    return f"{Decimal(value) / 100:,.2f}"


def variance(actual, planned, tolerance_bp):
    if planned == 0:
        return None, actual != 0
    difference = actual - planned
    percent = Decimal(difference) * 100 / planned
    return f"{percent:+.1f}%", abs(difference) * 10000 > planned * tolerance_bp
