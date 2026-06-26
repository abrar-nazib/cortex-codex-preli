"""Models for the QueueStorm Investigator ticket analysis pipeline.

Mirrors the Problem Statement request schema (§5):

  Ticket            — one customer complaint + its routing context.
  Transaction       — one entry in that ticket's `transaction_history` (§5.2),
                      owned by a Ticket (related_name="transaction_history").

Persistence is optional for the preliminary (the per-request analysis is
stateless), but the models are here so a future persist/audit step can store the
raw record + matched transactions without reshaping the contract.
"""
from __future__ import annotations

from django.db import models


class Ticket(models.Model):
    """A single customer complaint + its support-routing context (§5).

    `ticket_id` is the natural primary key — the harness issues it and it must
    be echoed back verbatim in the response (§6). Everything else mirrors the
    request fields; optionals default to empty so a bare {ticket_id, complaint}
    payload still persists cleanly.
    """

    ticket_id = models.CharField(max_length=64, primary_key=True)
    complaint = models.TextField()
    language = models.CharField(max_length=8, blank=True, default="")
    channel = models.CharField(max_length=32, blank=True, default="")
    user_type = models.CharField(max_length=16, blank=True, default="")
    campaign_context = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tickets"

    def __str__(self) -> str:
        return self.ticket_id


class Transaction(models.Model):
    """One entry in a Ticket's `transaction_history` (§5.2).

    `timestamp` is kept as an ISO-8601 string (not DateTimeField) because the
    spec defines it as a string and the investigator's matching logic operates
    on the raw value; no tz coercion needed for the preliminary. `amount` is
    Decimal for money safety even though the wire type is a number.
    """

    ticket = models.ForeignKey(
        Ticket,
        related_name="transaction_history",
        on_delete=models.CASCADE,
    )
    transaction_id = models.CharField(max_length=64)
    timestamp = models.CharField(max_length=32)
    type = models.CharField(max_length=32)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    counterparty = models.CharField(max_length=64)
    status = models.CharField(max_length=16)

    class Meta:
        db_table = "transactions"
        unique_together = (("ticket", "transaction_id"),)

    def __str__(self) -> str:
        return f"{self.transaction_id} ({self.type})"