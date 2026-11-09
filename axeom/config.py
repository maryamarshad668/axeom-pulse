"""Shared settings for the AXEOM PULSE data engine.

Everything that describes the *shape* of the data lives here, so that the
rest of the code never hardcodes client names, record counts or totals.
"""
from datetime import date

# The challenge's "today". Never use the computer clock for business logic.
REFERENCE_DATE = date(2026, 10, 6)

# ---------------------------------------------------------------------------
# Supported source files
# ---------------------------------------------------------------------------
# key            -> the logical dataset name used everywhere in the code
# filename       -> the name used in the original pack (used as a hint only)
# required       -> columns that must be present for the file to be accepted
# id_column      -> stable identifier; None means a key must be derived
# date_columns   -> {column: expected style}; "iso" or "slash"
# date_convention-> for "slash" columns only: the day/month order observed in
#                   the original pack. Used ONLY when a file gives no evidence
#                   of its own, and every such use is flagged (never silent).
# money_columns  -> columns holding money
# ---------------------------------------------------------------------------
SOURCES = {
    "leads": {
        "filename": "leads.csv",
        "required": ["lead_id", "created", "name", "business", "email", "phone",
                     "source", "first_reply", "status"],
        "id_column": "lead_id",
        "date_columns": {"created": "iso", "first_reply": "iso"},
        "money_columns": [],
    },
    "bookings": {
        "filename": "bookings.csv",
        "required": ["booking_id", "date", "client_name", "email", "service",
                     "status", "price"],
        "id_column": "booking_id",
        "date_columns": {"date": "slash"},
        "date_convention": {"date": "MDY"},
        "money_columns": ["price"],
    },
    "invoices": {
        "filename": "invoices.csv",
        "required": ["invoice_no", "bill_to", "email", "issued", "due", "amount",
                     "status", "memo"],
        "id_column": "invoice_no",
        "date_columns": {"issued": "iso", "due": "iso"},
        "money_columns": ["amount"],
    },
    "payments": {
        "filename": "payments.csv",
        "required": ["payment_id", "date", "payer", "payer_email", "amount",
                     "method", "reference"],
        "id_column": "payment_id",
        "date_columns": {"date": "slash"},
        "date_convention": {"date": "DMY"},
        "money_columns": ["amount"],
    },
    "production": {
        "filename": "production_board.csv",
        "required": ["video_id", "client", "title", "editor", "stage",
                     "notes_on_board", "revisions", "editor_hours",
                     "client_approved_on", "shoot_booking"],
        "id_column": "video_id",
        "date_columns": {"client_approved_on": "iso"},
        "money_columns": [],
    },
    "team": {
        "filename": "team.csv",
        "required": ["name", "role", "joined", "pay"],
        "id_column": "name",
        "date_columns": {"joined": "iso"},
        "money_columns": [],
    },
    "costs": {
        "filename": "costs.csv",
        "required": ["item", "cost", "note"],
        "id_column": "item",
        "date_columns": {},
        "money_columns": [],
    },
    "messages": {
        "filename": "messages.txt",
        "required": [],          # free text; structure checked line by line
        "id_column": None,       # no stable id -> derived message_key
        "date_columns": {},
        "money_columns": [],
    },
}

# Known status vocabularies (lower-case). Unknown values are kept and flagged,
# never silently rewritten.
STATUS_VALUES = {
    "leads": {"new", "talking", "won", "lost"},
    "bookings": {"completed", "cancelled", "upcoming"},
    "invoices": {"paid", "unpaid", "partial", "void"},
    "production": {"in edit", "in review", "in revision", "delivered"},
}

# Entity resolution thresholds (RapidFuzz scores are 0-100).
FUZZY_AUTO_LINK = 92       # at or above this (and clearly best) -> link
FUZZY_REVIEW = 80          # between REVIEW and AUTO_LINK -> human review
FUZZY_MARGIN = 5           # best must beat the runner-up by this much
DUPLICATE_CLIENT_SCORE = 85  # two separate clients this similar -> flag
