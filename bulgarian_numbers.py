"""Small, deterministic Bulgarian number formatting used by controlled contracts."""

from __future__ import annotations


_ONES = (
    "нула",
    "едно",
    "две",
    "три",
    "четири",
    "пет",
    "шест",
    "седем",
    "осем",
    "девет",
)
_TEENS = (
    "десет",
    "единадесет",
    "дванадесет",
    "тринадесет",
    "четиринадесет",
    "петнадесет",
    "шестнадесет",
    "седемнадесет",
    "осемнадесет",
    "деветнадесет",
)
_TENS = (
    "",
    "",
    "двадесет",
    "тридесет",
    "четиридесет",
    "петдесет",
    "шестдесет",
    "седемдесет",
    "осемдесет",
    "деветдесет",
)
_HUNDREDS = (
    "",
    "сто",
    "двеста",
    "триста",
    "четиристотин",
    "петстотин",
    "шестстотин",
    "седемстотин",
    "осемстотин",
    "деветстотин",
)


def bulgarian_integer_words(value: int) -> str:
    """Return contract-ready Bulgarian words for an integer up to 999,999,999."""

    if not 0 <= value <= 999_999_999:
        raise ValueError("The supported amount range is 0 to 999,999,999.")
    if value == 0:
        return _ONES[0]

    millions, remainder = divmod(value, 1_000_000)
    thousands, units = divmod(remainder, 1_000)
    groups: list[tuple[str, int]] = []
    if millions:
        prefix = _under_thousand_words(millions, gender="masculine")
        groups.append((f"{prefix} {'милион' if millions == 1 else 'милиона'}", millions))
    if thousands:
        if thousands == 1:
            groups.append(("хиляда", thousands))
        else:
            groups.append(
                (f"{_under_thousand_words(thousands, gender='feminine')} хиляди", thousands)
            )
    if units:
        groups.append((_under_thousand_words(units, gender="neuter"), units))

    if len(groups) > 1 and _needs_group_conjunction(groups[-1][1]):
        return " ".join(text for text, _ in groups[:-1]) + " и " + groups[-1][0]
    return " ".join(text for text, _ in groups)


def _under_thousand_words(value: int, *, gender: str) -> str:
    hundreds, remainder = divmod(value, 100)
    parts: list[str] = []
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if remainder:
        remainder_text = _under_hundred_words(remainder, gender=gender)
        if hundreds and _needs_group_conjunction(remainder):
            parts.append("и")
        parts.append(remainder_text)
    return " ".join(parts)


def _under_hundred_words(value: int, *, gender: str) -> str:
    if value < 10:
        if value == 1:
            return {"masculine": "един", "feminine": "една"}.get(gender, "едно")
        if value == 2 and gender == "masculine":
            return "два"
        return _ONES[value]
    if value < 20:
        return _TEENS[value - 10]
    tens, ones = divmod(value, 10)
    if not ones:
        return _TENS[tens]
    return f"{_TENS[tens]} и {_under_hundred_words(ones, gender=gender)}"


def _needs_group_conjunction(value: int) -> bool:
    """Bulgarian uses a joining 'и' before a final exact hundred/tens/small group."""

    return value < 20 or value % 10 == 0 or value % 100 == 0
