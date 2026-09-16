"""Shared money, date, and schema primitives. No model decides affordability."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

COLUMNS = ['request_id', 'amount_safe_to_pay', 'affordability_status',
           'recommended_payment_method', 'payment_plan', 'earliest_date_for_full_payment',
           'spending_changes_needed', 'decision_explanation']
ZERO = Decimal('0')
CENT = Decimal('.01')


class DataError(ValueError):
    pass


def money(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataError('Missing or invalid monetary amount') from exc
    if not result.is_finite() or result < 0:
        raise DataError('Money must be finite and nonnegative')
    return result


def fmt(value: Decimal) -> str:
    return format(value.quantize(CENT), 'f').rstrip('0').rstrip('.') or '0'


def floor_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_DOWN)


def day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise DataError('Invalid ISO date') from exc


def choices(value: str) -> set[str]:
    return set(filter(None, value.split('|')))


class Fact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_ids: list[str] = Field(min_length=1)
    event_id: str | None = None
    category: str | None = None
    action: Literal['amend', 'cancel', 'confirm', 'exclude', 'add']
    amount: str | None = None
    currency: str | None = None
    effective_date: str | None = None
    direction: Literal['credit', 'debit'] | None = None
    status: Literal['settled', 'scheduled', 'pending', 'cancelled', 'unrealized'] | None = None
    recurring: bool = False
    # For an explicitly stated percentage amendment, e.g. renewed rent +12%.
    multiplier: str | None = None
    note: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    facts: list[Fact] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class ChatIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['explain', 'request', 'help']
    requested_amount: str | None = None
    request_date: str | None = None
    desired_completion_date: str | None = None
    allows_partial_payment: bool | None = None
    request_type: Literal['purchase', 'travel', 'education', 'family_transfer',
                          'debt_repayment', 'investment', 'housing',
                          'emergency_expense', 'other'] = 'other'
    request_text: str = ''


@dataclass(frozen=True)
class Flow:
    when: date
    amount: Decimal  # signed: credit positive, debit negative
    source: str
    category: str
    recurring: bool = False


@dataclass(frozen=True)
class Adjustment:
    event_id: str
    kind: str
    new_amount: Decimal
    old_amount: Decimal

    def text(self):
        return (f'stop:{self.event_id}' if self.kind == 'stop'
                else f'reduce_to:{self.event_id}:{fmt(self.new_amount)}')


@dataclass
class Forecast:
    start: date
    end: date
    opening: Decimal
    minimum: Decimal
    flows: list[Flow]
    adjustable: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    method: str
    payments: tuple[tuple[date, Decimal], ...]
    changes: tuple[Adjustment, ...] = ()
    option_id: str = ''

    def rank(self):
        return (bool(self.changes), sum(a for _, a in self.payments),
                self.payments[0][0], len(self.payments), self.option_id,
                len(self.changes), tuple(c.text() for c in self.changes))
