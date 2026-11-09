"""Client identity: same client under different spellings, and no wrong merges.

The tests look records up by their content in the original Pack 208 files,
so no client name or count is hardcoded into the application itself."""
import pandas as pd
import pytest

from axeom.entity_resolution import resolve_clients
from axeom.normalizer import name_key


def _client(model, source, column, value):
    """Client id(s) of the records whose `column` equals `value`."""
    df = model.frames[source]
    id_col = {"leads": "lead_id", "bookings": "booking_id", "invoices": "invoice_no",
              "payments": "payment_id", "production": "video_id", "messages": "message_key"}[source]
    ids = df[df[column] == value][id_col]
    assert len(ids), f"no {source} record with {column} == {value!r}"
    return {model.resolution.client_of(source, i) for i in ids}


def test_every_mention_is_traceable(original_model):
    links = original_model.resolution.links
    assert (links["source_file"] != "").all()
    assert links["source_line"].astype(str).str.len().gt(0).all()
    assert set(links["method"]) <= {"email", "phone", "confirmed_alias", "announced_alias", "exact_name",
                                    "fuzzy", "booking_reference", "unmatched"}


def test_different_spellings_resolve_to_one_client(original_model):
    m = original_model
    spellings = [("bookings", "client_name", "Jared's Roofing LLC"),
                 ("bookings", "client_name", "Jareds Roofing"),
                 ("payments", "payer", "JARED KHAN"),
                 ("invoices", "bill_to", "Jared's Roofing")]
    clients = set().union(*[_client(m, *s) for s in spellings])
    assert len(clients) == 1 and "" not in clients


def test_personal_gmail_joins_business_through_shared_phone(original_model):
    m = original_model
    gmail = _client(m, "leads", "email", "aisha.chen.mail@gmail.com")
    business = _client(m, "leads", "email", "aisha@calmandcollected.fm")
    assert gmail == business
    links = m.resolution.links
    lead = m.frames["leads"][m.frames["leads"]["email"] == "aisha.chen.mail@gmail.com"]["lead_id"].iloc[0]
    assert links[links.record_id == lead]["method"].iloc[0] == "phone"


def test_payment_without_email_links_by_exact_name(original_model):
    m = original_model
    p = m.frames["payments"]
    row = p[(p["payer_email"] == "") & (p["payer"] == "LUIS FOSTER")].iloc[0]
    cid = m.resolution.client_of("payments", row["payment_id"])
    assert cid == _client(m, "leads", "email", "luis@bloomroofingco.com").pop()


def test_first_name_alone_does_not_decide(original_model):
    """Two different 'Jared's text us. The phone number decides, not the name."""
    m = original_model
    msgs = m.frames["messages"]
    walsh = _client(m, "leads", "email", "info@walshlaw.com").pop()
    roofing = _client(m, "leads", "email", "jared@jaredsroofingtx.com").pop()
    assert walsh != roofing
    for phone, expected in (("2145550109", walsh), ("9725550110", roofing)):
        keys = msgs[msgs["phone_norm"] == phone]["message_key"]
        assert {m.resolution.client_of("messages", k) for k in keys} == {expected}


def test_same_person_name_two_different_clients_not_merged(original_model):
    m = original_model
    dental = _client(m, "leads", "email", "hello@haledental.com")
    agency = _client(m, "leads", "email", "alana@kindredmktg.com")
    assert dental.isdisjoint(agency)                     # both contacts are called Alana Hale
    sofia_a = _client(m, "leads", "email", "sofia@lakesidecoffee.com")
    sofia_b = _client(m, "leads", "email", "sofia@khaninsurance.com")
    assert sofia_a.isdisjoint(sofia_b)                   # both contacts are called Sofia Khan
    review = m.resolution.review
    assert review["detail"].str.contains("alana hale").any()
    assert review["detail"].str.contains("sofia khan").any()


def test_similar_business_names_go_to_review_not_merged(original_model):
    m = original_model
    a = _client(m, "leads", "email", "nadia@stonebridgerealty.com")
    b = _client(m, "leads", "email", "nolan@stonebridgerg.com")
    assert a.isdisjoint(b)
    dup = m.resolution.review[m.resolution.review.kind == "possible_duplicate_clients"]
    assert dup["candidates"].str.contains(a.pop()).any()


def test_announced_rename_joins_old_and_new_email(original_model):
    """Phase 1 kept Pecan Gym / Pecan Athletics apart (shared phone, different
    domains). The client's own message announcing the new email is direct
    evidence, so they are joined, and the link says why."""
    m = original_model
    gym = _client(m, "leads", "email", "trent@pecangym.co")
    athletics = _client(m, "leads", "email", "trent@pecanathletics.com")
    assert gym == athletics
    links = m.resolution.links
    assert (links.method == "announced_alias").any()
    email_ann = links[(links.method == "announced_alias") & links.note.str.startswith("email")]
    assert len(email_ann) and email_ann.note.str.contains("trent@pecanathletics.com").all()


def test_shared_phone_with_conflicting_business_is_not_merged():
    """Without an announcement, a shared phone with different domains is NOT enough."""
    from axeom.normalizer import normalize_frame
    leads = pd.DataFrame([
        ["L1", "2026-01-01 10:00", "Sam Roe", "Roe Gym", "sam@roegym.com", "214-555-0001", "Google", "", "Won"],
        ["L2", "2026-02-01 10:00", "Sam Roe", "Roe Bakery", "sam@roebakery.com", "214-555-0001", "Google", "", "Won"]],
        columns=["lead_id", "created", "name", "business", "email", "phone", "source", "first_reply", "status"])
    leads["_source_file"], leads["_source_line"] = "leads.csv", [2, 3]
    res = resolve_clients({"leads": normalize_frame("leads", leads)})
    assert res.client_of("leads", "L1") != res.client_of("leads", "L2")
    assert (res.review.kind == "phone_conflict").any()


def test_agency_booking_for_its_customer_is_flagged(original_model):
    """Kindred (an agency) books shoots for its customers under its own email."""
    links = original_model.resolution.links
    flagged = links[links.needs_review & (links.email == "alana@kindredmktg.com")]
    assert len(flagged) >= 1
    assert flagged["note"].str.contains("another client").all()


def test_typo_email_joins_by_exact_name_and_same_domain_stem(original_model):
    m = original_model
    assert _client(m, "bookings", "email", "hello@haledental.con") == \
        _client(m, "leads", "email", "hello@haledental.com")


def test_production_rows_inherit_client_from_booking(original_model):
    m = original_model
    prod = m.frames["production"]
    row = prod[prod["shoot_booking"] != ""].iloc[0]
    assert m.resolution.client_of("production", row["video_id"]) == \
        m.resolution.client_of("bookings", row["shoot_booking"])


def test_no_client_is_hardcoded_count_is_derived(original_model):
    c = original_model.resolution.clients
    assert c["client_id"].is_unique
    emails = [e for cell in c["emails"] for e in cell.split(", ") if e]
    assert len(emails) == len(set(emails))               # every email belongs to one client


# ---------------------------------------------------------------- synthetic
def _frames(rows_bookings, rows_production=()):
    from axeom.normalizer import normalize_frame
    b = pd.DataFrame(rows_bookings, columns=["booking_id", "date", "client_name", "email", "service",
                                             "status", "price"])
    b["_source_file"], b["_source_line"] = "bookings.csv", range(2, len(b) + 2)
    p = pd.DataFrame(list(rows_production), columns=["video_id", "client", "title", "editor", "stage",
                                                     "notes_on_board", "revisions", "editor_hours",
                                                     "client_approved_on", "shoot_booking"])
    p["_source_file"], p["_source_line"] = "production_board.csv", range(2, len(p) + 2)
    return {"bookings": normalize_frame("bookings", b), "production": normalize_frame("production", p)}


def test_fuzzy_match_has_confidence_and_links_when_clear():
    f = _frames([["B1", "10/01/2026", "Lakeside Coffee Roasters", "sofia@lakeside.com", "Reels Pack", "upcoming", "900"]],
                [["V1", "Lakeside Coffee Roastrs", "Promo", "Ali", "In edit", "", "0", "1", "", ""]])
    res = resolve_clients(f)
    link = res.links[res.links.record_id == "V1"].iloc[0]
    assert link.method == "fuzzy"
    assert 92 <= link.confidence < 100
    assert link.client_id == res.client_of("bookings", "B1")


def test_uncertain_fuzzy_match_goes_to_review():
    # 'Oak Med Spa' is equally close to two different clients: never guess
    f = _frames([["B1", "10/01/2026", "Oak Lane Med Spa", "a@oaklane.com", "Reels Pack", "upcoming", "900"],
                 ["B2", "10/02/2026", "Oak Leaf Med Spa", "b@oakleaf.com", "Reels Pack", "upcoming", "900"]],
                [["V1", "Oak Med Spa", "Promo", "Ali", "In edit", "", "0", "1", "", ""]])
    res = resolve_clients(f)
    link = res.links[res.links.record_id == "V1"].iloc[0]
    assert link.client_id == "" and link.needs_review
    item = res.review[res.review.kind == "fuzzy_uncertain"].iloc[0]
    assert "Oak Lane Med Spa" in item.candidates and "Oak Leaf Med Spa" in item.candidates


def test_confirmed_alias_decision_is_applied(original_dir):
    from axeom import DataStore, build_model
    import os
    import tempfile
    store, _ = DataStore.from_folder(original_dir)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "decisions.csv")
        pd.DataFrame([{"value_type": "handle", "value": "@juniperwealthpartntx",
                       "target_email": "vramirez@juniperwp.com", "decision": "same",
                       "note": "owner confirmed"}]).to_csv(path, index=False)
        model = build_model(store.raw, decisions_path=path)
    links = model.resolution.links
    hit = links[links.handle == "juniperwealthpartntx"].iloc[0]
    assert hit.method == "confirmed_alias"
    assert hit.client_id == links[links.email == "vramirez@juniperwp.com"].client_id.iloc[0]


def test_different_decision_blocks_a_merge(original_dir):
    from axeom import DataStore, build_model
    import os
    import tempfile
    store, _ = DataStore.from_folder(original_dir)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "decisions.csv")
        pd.DataFrame([{"value_type": "name", "value": "hale dental", "target_email": "hello@haledental.con",
                       "decision": "different", "note": "test"}]).to_csv(path, index=False)
        model = build_model(store.raw, decisions_path=path)
    links = model.resolution.links
    typo = links[links.email == "hello@haledental.con"].client_id.iloc[0]
    main = links[links.email == "hello@haledental.com"].client_id.iloc[0]
    assert typo != main
