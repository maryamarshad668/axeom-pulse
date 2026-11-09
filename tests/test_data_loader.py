"""Loading files: every original file, bad files, raw values, messages."""
import os

import pandas as pd

from axeom.config import SOURCES
from axeom.data_loader import detect_source, load_csv, load_file, load_folder, load_messages
from axeom.normalizer import normalize_frame
from conftest import read_bytes


def _data_lines(path):
    with open(path, encoding="utf-8-sig") as fh:
        return [l for l in fh.read().splitlines() if l.strip()]


def test_every_original_file_loads(original_dir):
    results = load_folder(original_dir)
    expected = {k for k, s in SOURCES.items()}
    assert set(results) == expected
    for key, res in results.items():
        assert res.ok, (key, res.errors)
        assert not res.rejected_rows, (key, res.rejected_rows[:3])
        # row count matches the file itself (header excluded for CSV)
        lines = _data_lines(os.path.join(original_dir, SOURCES[key]["filename"]))
        expected_rows = len(lines) if key == "messages" else len(lines) - 1
        assert len(res.data) == expected_rows, key
        assert {"_source_file", "_source_line"} <= set(res.data.columns)


def test_raw_values_are_preserved(original_dir):
    res = load_csv(os.path.join(original_dir, "bookings.csv"))
    raw_prices = set(res.data["price"])
    assert any(p.startswith("$") for p in raw_prices)      # '$1,500' kept as typed
    norm = normalize_frame("bookings", res.data)
    assert list(norm["price"]) == list(res.data["price"])   # raw column untouched
    assert norm["price_value"].notna().all()
    one = norm[norm["price"] == "$1,500"].iloc[0]
    assert one["price_value"] == 1500.0


def test_bom_crlf_and_header_whitespace():
    raw = "﻿ team_name ,role,joined,pay\r\nZaid,Owner,2023-01-01,\r\n".replace("team_name", "name")
    res = load_csv(raw.encode("utf-8"), file_name="team.csv")
    assert res.ok, res.errors
    assert list(res.data.columns[:4]) == ["name", "role", "joined", "pay"]
    assert res.data.loc[0, "name"] == "Zaid"


def test_windows_encoding_is_decoded():
    raw = "name,role,joined,pay\nJos\xe9,Editor,2024-01-01,$30/hour\n".encode("cp1252")
    res = load_csv(raw, file_name="team.csv")
    assert res.ok
    assert res.data.loc[0, "name"] == "José"
    assert any("cp1252" in w for w in res.warnings)


def test_missing_required_column_is_reported_not_crashing():
    raw = read_bytes("invoices.csv").decode("utf-8").replace(",amount,", ",total,", 1)
    res = load_csv(raw.encode(), file_name="invoices.csv")
    assert not res.ok
    assert res.data is None
    assert any("amount" in e for e in res.errors)


def test_unknown_file_is_rejected_with_reason():
    res = load_file(b"a,b,c\n1,2,3\n", file_name="random.csv")
    assert not res.ok and res.errors
    res = load_file(b"%PDF-1.4", file_name="report.pdf")
    assert not res.ok and "only .csv and .txt" in res.errors[0]
    res = load_file(b"", file_name="leads.csv")
    assert not res.ok and "empty" in res.errors[0]


def test_file_is_identified_by_columns_not_name():
    raw = read_bytes("payments.csv")
    res = load_file(raw, file_name="bank_export_october.csv")
    assert res.ok and res.source == "payments"
    key, why = detect_source("leads.csv", ["payment_id", "date", "payer", "payer_email",
                                           "amount", "method", "reference"])
    assert key == "payments" and "using the columns" in why


def test_rows_without_id_are_rejected():
    raw = ("booking_id,date,client_name,email,service,status,price\n"
           "B900,10/01/2026,New Co,new@co.com,Reels Pack,upcoming,900\n"
           ",10/02/2026,No Id,noid@co.com,Reels Pack,upcoming,900\n")
    res = load_csv(raw.encode(), file_name="bookings.csv")
    assert res.ok
    assert len(res.data) == 1
    assert res.rejected_rows[0]["line"] == 3
    assert "missing booking_id" in res.rejected_rows[0]["reason"]


def test_malformed_csv_returns_error():
    raw = 'lead_id,created,name\nL1,"2026-01-01,broken\n'
    res = load_csv(raw.encode(), file_name="leads.csv")
    assert not res.ok and res.errors


def test_messages_are_parsed(original_dir):
    res = load_messages(os.path.join(original_dir, "messages.txt"))
    df = res.data
    assert res.ok and not res.rejected_rows
    assert df["message_key"].is_unique
    voice = df[df["channel"] == "VOICE NOTE"]
    assert len(voice) > 0
    assert (voice["voice_duration"] != "").all()
    assert not voice["text"].str.startswith('"').any()            # quotes stripped
    assert (voice["contact_phone"] != "").all()
    broadcast = df[df["counterpart"].str.contains("broadcast")]
    assert len(broadcast) == 1 and broadcast.iloc[0]["contact_other"]
    outbound = df[df["direction"] == "outbound"]
    assert len(outbound) >= 1
    # a transcript containing ':' is not cut in the wrong place
    colon = df[df["text"].str.contains("car accident: first 3 steps")]
    assert len(colon) >= 1
    assert (colon["contact_name"] == "Jared").all()
    assert (colon["contact_phone"] != "").all()


def test_bad_message_lines_are_reported():
    raw = ("[2026-10-01 09:00] EMAIL from a@b.com: hello\n"
           "this line is not a message\n"
           "[2026-10-01 09:05] WHATSAPP from +1 214-555-0000 (Sam): hi\n")
    res = load_messages(raw.encode(), file_name="messages.txt")
    assert res.ok and len(res.data) == 2
    assert res.rejected_rows[0]["line"] == 2


def test_same_message_twice_in_one_file_is_kept_once():
    line = "[2026-10-01 09:00] EMAIL from a@b.com: hello there\n"
    res = load_messages((line + line.replace("hello there", "hello   there")).encode(), file_name="m.txt")
    assert len(res.data) == 1
    assert "duplicate" in res.rejected_rows[0]["reason"]
