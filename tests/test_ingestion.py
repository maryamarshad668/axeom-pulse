"""Uploading new files: duplicates, corrections, new clients, rejected records."""
import os
import tempfile

from axeom import DataStore, build_model
from axeom.ingestion import reports_table
from conftest import read_bytes


def test_uploading_the_same_files_twice_changes_nothing(fresh_store):
    before = {k: len(v) for k, v in fresh_store.raw.items()}
    files = [(n, read_bytes(n)) for n in ("invoices.csv", "payments.csv", "messages.txt", "leads.csv")]
    reports = fresh_store.ingest(files)
    for r in reports:
        assert r.accepted, r.errors
        assert r.inserted == 0 and r.updated == 0
        assert r.duplicates > 0
    assert {k: len(v) for k, v in fresh_store.raw.items()} == before


def test_messages_reupload_with_windows_line_endings_is_deduplicated(fresh_store):
    data = read_bytes("messages.txt").decode("utf-8").replace("\n", "\r\n").encode("utf-8")
    rep = fresh_store.ingest([("messages_export.txt", b"\xef\xbb\xbf" + data)])[0]
    assert rep.inserted == 0 and rep.duplicates == len(fresh_store.raw["messages"])


def test_a_corrected_record_updates_and_keeps_history(fresh_store):
    inv = fresh_store.raw["invoices"]
    target = inv[inv["status"] == "unpaid"].iloc[0]
    header = "invoice_no,bill_to,email,issued,due,amount,status,memo\n"
    row = f'{target["invoice_no"]},{target["bill_to"]},{target["email"]},{target["issued"]},' \
          f'{target["due"]},{target["amount"]},paid,"{target["memo"]}"\n'
    rep = fresh_store.ingest([("invoices_fix.csv", (header + row).encode())])[0]
    assert rep.updated == 1 and rep.inserted == 0
    now = fresh_store.raw["invoices"]
    assert len(now) == len(inv)
    updated = now[now["invoice_no"] == target["invoice_no"]].iloc[0]
    assert updated["status"] == "paid"
    assert updated["_source_file"] == "invoices_fix.csv"
    hist = [h for h in fresh_store.history if h["action"] == "updated"]
    assert hist and '"status": "unpaid"' in hist[-1]["old"] and '"status": "paid"' in hist[-1]["new"]


def test_new_client_appears_after_upload(fresh_store):
    before = build_model(fresh_store.raw).resolution.clients
    new = ("booking_id,date,client_name,email,service,status,price\n"
           "B901,10/20/2026,Brand New Bakery,owner@brandnewbakery.com,Reels Pack,upcoming,$900\n")
    rep = fresh_store.ingest([("bookings_oct.csv", new.encode())])[0]
    assert rep.inserted == 1
    after = build_model(fresh_store.raw).resolution.clients
    assert len(after) == len(before) + 1
    assert after["emails"].str.contains("owner@brandnewbakery.com").any()


def test_new_record_for_existing_client_links_to_that_client(fresh_store):
    model = build_model(fresh_store.raw)
    existing = model.resolution.links
    cid = existing[existing.email == "greg@cedar.build"].client_id.iloc[0]
    new = ("payment_id,date,payer,payer_email,amount,method,reference\n"
           "P9001,15/10/2026,CEDAR BLDRS,greg@cedar.build,900.00,ACH,INV-9999\n")
    fresh_store.ingest([("bank_oct.csv", new.encode())])
    model = build_model(fresh_store.raw)
    assert model.resolution.client_of("payments", "P9001") == cid
    q = model.quality
    assert ((q.record_id == "P9001") & (q.issue_type == "missing_reference")).any()


def test_same_id_twice_with_different_content_is_not_applied(fresh_store):
    rows = ("lead_id,created,name,business,email,phone,source,first_reply,status\n"
            "L901,2026-10-01 10:00,Ann,Ann Co,ann@annco.com,214-555-0001,Google,,New\n"
            "L901,2026-10-01 10:00,Ann,Ann Co,ann@annco.com,214-555-0001,Google,,Won\n")
    rep = fresh_store.ingest([("leads_new.csv", rows.encode())])[0]
    assert rep.inserted == 0 and len(rep.ambiguous) == 1
    assert "L901" not in set(fresh_store.raw["leads"]["lead_id"])


def test_invalid_amount_row_is_rejected_valid_rows_kept(fresh_store):
    rows = ("payment_id,date,payer,payer_email,amount,method,reference\n"
            "P9101,15/10/2026,A,a@a.com,abc,ACH,\n"
            "P9102,16/10/2026,B,b@b.com,300.00,ACH,\n")
    rep = fresh_store.ingest([("pay.csv", rows.encode())])[0]
    assert rep.inserted == 1 and len(rep.rejected) == 1
    assert rep.rejected[0]["record_id"] == "P9101" and rep.rejected[0]["line"] == 2


def test_file_with_missing_columns_is_rejected_whole(fresh_store):
    before = len(fresh_store.raw["bookings"])
    rows = "booking_id,date,client_name\nB950,10/01/2026,X\n"
    rep = fresh_store.ingest([("bookings.csv", rows.encode())])[0]
    assert not rep.accepted and rep.errors
    assert len(fresh_store.raw["bookings"]) == before
    assert not reports_table([rep]).empty


def test_new_file_with_only_ambiguous_dates_is_flagged(fresh_store):
    rows = ("payment_id,date,payer,payer_email,amount,method,reference\n"
            "P9201,03/10/2026,Someone,s@s.com,300.00,ACH,\n")
    fresh_store.ingest([("tiny.csv", rows.encode())])
    model = build_model(fresh_store.raw)
    pay = model.frames["payments"]
    row = pay[pay["payment_id"] == "P9201"].iloc[0]
    assert row["date_date_status"] == "assumed_source_convention"
    assert str(row["date_date"].date()) == "2026-10-03"          # DMY, like the pack's payments
    q = model.quality
    assert ((q.record_id == "P9201") & (q.issue_type == "assumed_date_format")).any()


def test_save_and_load_round_trip(fresh_store):
    fresh_store.ingest([("x.csv", b"name,role,joined,pay\nLina,Editor,2026-10-01,$25/hour\n")])
    with tempfile.TemporaryDirectory() as tmp:
        fresh_store.save(tmp)
        again = DataStore.load(tmp)
    assert {k: len(v) for k, v in again.raw.items()} == {k: len(v) for k, v in fresh_store.raw.items()}
    assert len(again.history) == len(fresh_store.history)
    a, b = build_model(fresh_store.raw), build_model(again.raw)
    assert len(a.resolution.clients) == len(b.resolution.clients)
