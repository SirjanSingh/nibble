"""Collector contract.

A collector is a pluggable adapter that yields normalized Record objects.
`kind` is "local" (cheap, polled often) or "remote" (API, polled rarely).
`available()` reports whether it can run (e.g. key present).
"""
from __future__ import annotations

from typing import Iterable, Protocol

from ..pricing import PricingTable
from ..store import Record, Store


class Collector(Protocol):
    name: str
    kind: str  # "local" | "remote"

    def available(self) -> tuple[bool, str]: ...

    def collect(self, store: Store, pricing: PricingTable) -> Iterable[Record]: ...
