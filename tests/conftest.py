import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ORIGINAL = os.path.join(ROOT, "data", "original")

from axeom import DataStore, build_model  # noqa: E402


@pytest.fixture(scope="session")
def original_dir():
    return ORIGINAL


@pytest.fixture(scope="session")
def original_model():
    """The original Pack 208 data, loaded and resolved once for all tests."""
    store, _ = DataStore.from_folder(ORIGINAL)
    return build_model(store.raw)


@pytest.fixture
def fresh_store():
    """A new store with the original data, safe to modify inside one test."""
    store, _ = DataStore.from_folder(ORIGINAL)
    return store


def read_bytes(name):
    with open(os.path.join(ORIGINAL, name), "rb") as fh:
        return fh.read()


@pytest.fixture(scope="session")
def analysis(original_model):
    """Phase 2: the six answers computed once on the original pack."""
    from axeom.analysis import run_all
    return run_all(original_model)


MINI_PACK = {
    "leads.csv": "lead_id,created,name,business,email,phone,source,first_reply,status\n"
                 "L1,2026-06-01 10:00,Ann Lee,Ann Bakery,ann@annbakery.com,214-555-0001,Referral,2026-06-01,Won\n"
                 "L2,2026-06-02 10:00,Bob Ray,Ray Plumbing,bob@rayplumbing.com,214-555-0002,Instagram,2026-06-03,Won\n"
                 "L3,2026-06-03 10:00,Cy Doe,Doe Cafe,cy@doecafe.com,214-555-0003,Instagram,,New\n"
                 "L4,2026-06-04 10:00,spam,,spam@spam.io,,Instagram,,New\n",
    "bookings.csv": "booking_id,date,client_name,email,service,status,price\n"
                    "B1,06/10/2026,Ann Bakery,ann@annbakery.com,Reels Pack,completed,$900\n"
                    "B2,07/10/2026,Ann Bakery,ann@annbakery.com,Reels Pack,completed,$900\n"
                    "B3,06/12/2026,Ray Plumbing,bob@rayplumbing.com,Reels Pack,completed,900\n"
                    "B4,07/14/2026,Ray Plumbing,bob@rayplumbing.com,Retainer Shoot,completed,0\n",
    "invoices.csv": "invoice_no,bill_to,email,issued,due,amount,status,memo\n"
                    "INV-1,Ann Bakery,ann@annbakery.com,2026-06-11,2026-06-25,900.00,paid,B1 Reels Pack\n"
                    "INV-2,Ray Plumbing,bob@rayplumbing.com,2026-06-13,2026-06-27,900.00,unpaid,\n"
                    "INV-3,Ray Plumbing,bob@rayplumbing.com,2026-07-01,2026-07-15,2500.00,paid,Monthly retainer July\n"
                    "INV-4,Ann Bakery,ann@annbakery.com,2026-09-01,2026-09-15,,unpaid,\n",
    "payments.csv": "payment_id,date,payer,payer_email,amount,method,reference\n"
                    "P1,20/06/2026,ANN BAKERY,ann@annbakery.com,900.00,ACH,INV-1\n"
                    "P2,10/07/2026,RAY PLUMBING,bob@rayplumbing.com,2500.00,ACH,\n"
                    "P3,15/07/2026,Someone Else,other@else.com,900.00,ACH,\n"
                    "P4,16/07/2026,RAY PLUMBING,bob@rayplumbing.com,450.00,Zelle,half now\n",
    "production_board.csv": "video_id,client,title,editor,stage,notes_on_board,revisions,editor_hours,"
                            "client_approved_on,shoot_booking\n"
                            "V1,Ann Bakery,Bread tour,Ali,Delivered,Captions bigger,1,3.0,2026-06-20,B1\n"
                            "V2,Ann Bakery,Cake reveal,Ali,Delivered,,0,2.0,2026-07-20,B2\n"
                            "V3,Ray Plumbing,Leak tips,Ali,Delivered,,0,2.0,,B3\n",
    "messages.txt": "[2026-06-15 10:00] EMAIL from ann@annbakery.com: Please make the captions bigger on bread tour, "
                    "they're hard to read on my phone.\n"
                    "[2026-07-15 10:00] VOICE NOTE from +1 214-555-0001 (Ann), 0:30. Transcript: \"Can cake reveal "
                    "have a calmer song? This one feels too hype for us.\"\n"
                    "[2026-07-16 10:00] WHATSAPP from +1 214-555-0002 (Bob): Can you cut the first 3 seconds of the "
                    "one we shot last month? It starts slow.\n",
    "team.csv": "name,role,joined,pay\nAli,Editor,2024-01-01,$35/hour\n",
    "costs.csv": 'item,cost,note\nStudio time,$40/hour,"Content Day uses 6 hours, Reels Pack 3, Retainer Shoot 4"\n',
}


@pytest.fixture
def mini_analysis():
    """A tiny hand-made pack where every right answer is known in advance."""
    from axeom.analysis import run_all
    store = DataStore()
    store.ingest([(n, t.encode()) for n, t in MINI_PACK.items()])
    return run_all(build_model(store.raw))
