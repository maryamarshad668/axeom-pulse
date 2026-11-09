"""Tests for the Phase 2 review fixes (each one was a wrong or missing result)."""
import pandas as pd

from axeom import DataStore, build_model
from axeom.analysis import run_all
from axeom.analysis.communication_analysis import estimate_video_dates
from axeom.entity_resolution import handle_score, resolve_clients
from axeom.normalizer import normalize_frame


def _req(analysis, ts):
    r = analysis.comms.requests
    hit = r[r.timestamp == ts]
    assert len(hit) == 1, ts
    return hit.iloc[0]


# ---------------------------------------------------------------- identity
def test_announced_new_number_links_unknown_sender(original_model):
    """Brooke emailed 'our new office number is 214-555-0121'; the WhatsApp
    later signed only 'Brooke' from that number is Apex Med Spa."""
    links = original_model.resolution.links
    apex = links[links.email == "brooke@apexmedspa.com"].client_id.iloc[0]
    from_new = links[(links.source == "messages") & (links.phone == "2145550121")]
    assert len(from_new) and (from_new.client_id == apex).all()
    assert (from_new.method == "announced_alias").all()


def test_referral_phone_is_not_an_announcement():
    """'His number is ... Call him!' passes on someone else's number."""
    msgs = pd.DataFrame([{
        "message_key": "M1", "timestamp": "2026-09-18 09:10", "channel": "WHATSAPP", "direction": "inbound",
        "counterpart": "+1 214-555-0109 (Jared)", "voice_duration": "", "raw_line": "",
        "text": "My cousin runs a shop, he needs videos. His number is 972-555-0999. Call him!",
        "contact_email": "", "contact_phone": "+1 214-555-0109", "contact_handle": "", "contact_name": "Jared",
        "contact_other": "", "_source_file": "messages.txt", "_source_line": 1}])
    leads = pd.DataFrame([
        ["L1", "2026-01-01 10:00", "Jared Walsh", "Walsh Law", "info@walshlaw.com", "214-555-0109", "Meta ad", "", "Won"],
        ["L2", "2026-09-20 10:00", "Mateo Walsh", "Walsh Tire", "mateo@walshtire.com", "972-555-0999", "Referral", "", "New"]],
        columns=["lead_id", "created", "name", "business", "email", "phone", "source", "first_reply", "status"])
    leads["_source_file"], leads["_source_line"] = "leads.csv", [2, 3]
    res = resolve_clients({"leads": normalize_frame("leads", leads), "messages": normalize_frame("messages", msgs)})
    assert res.client_of("leads", "L1") != res.client_of("leads", "L2")


def test_instagram_handles_with_suffixes():
    assert handle_score("juniperwealthpartntx", "juniperwealthpartners") >= 92
    assert handle_score("castillosautospatx", "castillosautospa") == 100
    assert handle_score("instasupportteam", "juniperwealthpartners") < 80


def test_every_real_handle_is_linked(original_model):
    links = original_model.resolution.links
    dms = links[(links.source == "messages") & (links.handle != "")]
    unlinked = set(dms[dms.client_id == ""].handle)
    assert unlinked == {"instasupportteam"}                     # the phishing DM only


# ---------------------------------------------------------------- requests
def test_request_words_are_not_read_as_a_title(analysis):
    """'The patient story video: add the line 'walk-ins welcome'' is about
    Patient story (V043, which carries the note), not the video titled
    'Walk-ins welcome'."""
    r = _req(analysis, "2026-05-27 14:57")
    assert r.video_id == "V043" and r.classification == "completed"


def test_cards_without_booking_get_estimated_dates(original_model):
    from axeom.analysis.context import make_context
    ctx = make_context(original_model)
    prod = ctx.frames["production"].copy()
    bk = ctx.frames["bookings"].set_index("booking_id")
    prod["shoot_date"] = prod.shoot_booking.map(bk.date_date)
    est = estimate_video_dates(prod)
    assert est.notna().all()
    assert est.min() >= pd.Timestamp("2026-01-01")              # no 1970 dates (unit bug)
    v027 = est[prod.video_id == "V027"].iloc[0]                  # sits between cards of the 11 May shoot
    assert v027 == pd.Timestamp("2026-05-11")


def test_repeated_title_prefers_the_card_with_the_note(analysis):
    r = _req(analysis, "2026-07-17 08:37")                       # stumble on 'retirement myths'
    assert r.video_id == "V137" and r.classification == "completed"


def test_last_week_request_identified_by_follow_up(analysis):
    r = _req(analysis, "2026-05-29 21:31")                       # 'the one we shot last week'
    assert r.video_id == "V027" and r.classification == "confirmed_lost"
    assert "follow-up" in r.video_match


def test_standing_instruction_and_complaint(analysis):
    r = _req(analysis, "2026-09-01 21:42")                       # 'say Juniper Wealth Partners, the full name'
    assert r.classification == "confirmed_lost" and r.client == "Juniper Wealth Partners"
    req = analysis.comms.requests
    assert not (req.timestamp == "2026-09-09 14:43").any()      # 'I asked for the full name' is a complaint
    fu = analysis.comms.followups
    assert (fu.timestamp == "2026-09-09 14:43").any()


def test_voice_notes_are_the_main_loss(analysis):
    ch = analysis.comms.by_channel
    assert ch.loc["voice note", "lost_share"] > 0.8
    assert ch.loc["whatsapp", "lost_share"] < 0.1


# ---------------------------------------------------------------- money
RESCHEDULE_PACK = {
    "bookings.csv": "booking_id,date,client_name,email,service,status,price\n"
                    "B1,09/10/2026,Ann Co,ann@anco.com,Content Day,cancelled,1500\n"
                    "B2,09/20/2026,Ann Co,ann@anco.com,Content Day,completed,1500\n",
    "invoices.csv": "invoice_no,bill_to,email,issued,due,amount,status,memo\n"
                    "INV-1,Ann Co,ann@anco.com,2026-09-05,2026-09-19,1500.00,paid,B1 Content Day\n"
                    "INV-2,Ann Co,ann@anco.com,2026-09-18,2026-10-02,1500.00,unpaid,\n",
    "payments.csv": "payment_id,date,payer,payer_email,amount,method,reference\n"
                    "P1,08/09/2026,ANN CO,ann@anco.com,1500.00,ACH,INV-1\n",
    "messages.txt": "[2026-09-10 08:00] WHATSAPP from ann@anco.com: We have to cancel today's shoot. "
                    "Can we move it? We already paid.\n",
}


def _run(pack):
    store = DataStore()
    store.ingest([(n, t.encode()) for n, t in pack.items()])
    return run_all(build_model(store.raw))


def test_paid_cancelled_shoot_that_was_moved_owes_nothing():
    a = _run(RESCHEDULE_PACK)
    f = a.finance
    assert f.we_owe.empty
    inv = f.invoices.set_index("invoice_no")
    assert inv.loc["INV-2", "true_status"] == "paid" and inv.loc["INV-2", "balance"] == 0
    assert inv.loc["INV-1", "true_status"] == "billed_cancelled_booking"
    assert f.summary["overdue_total"] == 0                      # INV-1 is not overdue either
    te = f.tool_errors.set_index("invoice_no")
    assert te.loc["INV-2", "kind"] == "status_wrong_unpaid"
    assert f.never_billed.empty                                  # B2 is covered


def test_paid_cancelled_shoot_not_rebooked_is_a_refund():
    pack = dict(RESCHEDULE_PACK)
    pack["bookings.csv"] = ("booking_id,date,client_name,email,service,status,price\n"
                            "B1,09/10/2026,Ann Co,ann@anco.com,Content Day,cancelled,1500\n")
    pack["invoices.csv"] = ("invoice_no,bill_to,email,issued,due,amount,status,memo\n"
                            "INV-1,Ann Co,ann@anco.com,2026-09-05,2026-09-19,1500.00,paid,B1 Content Day\n")
    a = _run(pack)
    owe = a.finance.we_owe
    assert len(owe) == 1 and owe.amount.iloc[0] == 1500
    assert owe.status.iloc[0] == "inferred"                      # a refund is a judgement, not a fact


# ---------------------------------------------------------------- answers
def test_limitations_are_computed_not_typed(analysis):
    from axeom.analysis.reports import cannot_tell
    lines = cannot_tell(analysis)
    joined = " ".join(lines)
    assert "Brooke" not in joined                                # resolved by the announcement rule
    assert "INV-1060" in joined                                  # the unexplained retainer amount
    assert "@instasupportteam" in joined
