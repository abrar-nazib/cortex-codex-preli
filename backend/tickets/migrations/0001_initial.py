"""Initial schema for the tickets app: Ticket + Transaction (§5)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Ticket",
            fields=[
                ("ticket_id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("complaint", models.TextField()),
                ("language", models.CharField(blank=True, default="", max_length=8)),
                ("channel", models.CharField(blank=True, default="", max_length=32)),
                ("user_type", models.CharField(blank=True, default="", max_length=16)),
                ("campaign_context", models.CharField(blank=True, default="", max_length=128)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "tickets"},
        ),
        migrations.CreateModel(
            name="Transaction",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("transaction_id", models.CharField(max_length=64)),
                ("timestamp", models.CharField(max_length=32)),
                ("type", models.CharField(max_length=32)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("counterparty", models.CharField(max_length=64)),
                ("status", models.CharField(max_length=16)),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transaction_history",
                        to="tickets.ticket",
                    ),
                ),
            ],
            options={
                "db_table": "transactions",
                "unique_together": {("ticket", "transaction_id")},
            },
        ),
    ]