"""Dates, money, phones and names."""
import pandas as pd

from axeom.normalizer import (name_key, normalize_email, normalize_phone, parse_date_column,
                              parse_money)


def test_ambiguous_dates_resolved_by_evidence_in_same_file():
    # 24/04/2026 can only be day-first, so 05/04/2026 in the same file is 5 April
    out = parse_date_column(["24/04/2026", "05/04/2026"])
    assert out.loc[1, "value"] == pd.Timestamp("2026-04-05")
    assert out.loc[1, "status"] == "resolved_by_file_evidence"
    # 04/13/2026 can only be month-first, so 04/05/2026 is 5 April
    out = parse_date_column(["04/13/2026", "04/05/2026"])
    assert out.loc[1, "value"] == pd.Timestamp("2026-04-05")


def test_ambiguous_dates_without_evidence_are_not_guessed():
    out = parse_date_column(["05/04/2026", "03/02/2026"])
    assert out["value"].isna().all()
    assert set(out["status"]) == {"ambiguous"}


def test_convention_is_used_only_with_a_flag():
    out = parse_date_column(["05/04/2026"], convention="DMY")
    assert out.loc[0, "value"] == pd.Timestamp("2026-04-05")
    assert out.loc[0, "status"] == "assumed_source_convention"


def test_file_mixing_both_orders_leaves_ambiguous_dates_empty():
    out = parse_date_column(["24/04/2026", "04/24/2026", "05/04/2026"], convention="DMY")
    assert out.loc[0, "value"] == pd.Timestamp("2026-04-24")
    assert out.loc[1, "value"] == pd.Timestamp("2026-04-24")
    assert pd.isna(out.loc[2, "value"]) and out.loc[2, "status"] == "ambiguous"


def test_invalid_and_blank_dates():
    out = parse_date_column(["31/02/2026", "2026-13-01", "next tuesday", "", "2026-04-01 19:05"])
    assert list(out["status"]) == ["invalid", "invalid", "invalid", "blank", "ok"]
    assert out.loc[4, "value"] == pd.Timestamp("2026-04-01 19:05")


def test_money_never_turns_missing_into_zero():
    assert parse_money("$1,500") == (1500.0, "ok")
    assert parse_money("300") == (300.0, "ok")
    assert parse_money("(225.00)") == (-225.0, "ok")
    assert parse_money("") == (None, "blank")
    assert parse_money("N/A") == (None, "invalid")
    assert parse_money("0") == (0.0, "ok")


def test_phone_and_email():
    assert normalize_phone("+1 972-555-0186") == "9725550186"
    assert normalize_phone("(972) 555 0186") == "9725550186"
    assert normalize_phone("555-0186") == ""
    assert normalize_email("  Hello@HaleDental.com ") == "hello@haledental.com"


def test_name_keys():
    assert name_key("Jared's Roofing LLC") == name_key("Jareds Roofing") == "jareds roofing"
    assert name_key("Calm & Collected") == name_key("Calm and Collected")
    assert name_key("Dr. Alana Hale") == name_key("Alana Hale DDS") == "alana hale"
    assert name_key("The Parkside Lab") == "parkside lab"
    assert name_key("Cedar & Co Builders") != ""          # 'co' in the middle is kept
