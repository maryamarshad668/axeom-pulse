# AXEOM PULSE: data engine , analysis and the owner's app

Connects Axeom's separate files into one source of truth. It loads leads, bookings, invoices, payments, the
production board, messages, team and costs. It then finds out which records belong to the same client, and
reports every data problem with its file and line. It also accepts new files without creating duplicates.

Phase 1 is the engine; Phase 2 answers the six challenge questions with an evidence ledger
(`run_analysis.py`). Phase 3 is the owner's app (`app.py`): phone first, one thing to do first, yes/no
suggestions that never send anything by themselves, every client linked to all its records, and an
"Add new files" page that recalculates everything.

## Project layout

```
axeom-pulse/
  app.py                    the owner's app (Streamlit), layout only
  .streamlit/config.toml    theme, upload size, minimal toolbar
  requirements.txt
  data/
    original/               the Pack 208 files, untouched
    match_decisions.csv     owner decisions for the review list (empty to start)
  axeom/
    config.py               reference date, file schemas, thresholds
    data_loader.py          read files safely, validate columns, parse messages
    normalizer.py           names, emails, phones, dates, money, statuses
    entity_resolution.py    client identity (email > phone > alias > name > fuzzy)
    data_quality.py         the data quality report
    ingestion.py            "Add new files": merge, dedupe, update, history
    pipeline.py             raw data -> normalised -> clients -> quality
    analysis/               Phase 2: the six challenge questions
      context.py              client ids on every record
      evidence.py             evidence ledger (every figure -> file, line, record)
      client_analysis.py      Q1 real clients, payers, profiles
      financial_analysis.py   Q2 overdue, we owe, never billed, billing tool errors
      communication_analysis.py  Q3 lost requests, unreplied leads, referrals
      churn_analysis.py       Q4 churn risk checklist
      marketing_analysis.py   Q5 channels (revenue, collected, margin after delivery cost)
      owner_priorities.py     Q6 Monday's three actions
      reports.py              CSV reports, answers.json, analysis_report.md
    workspace.py            Phase 3: original pack + saved uploads -> rebuild; undo, reset, identity answers
    suggestions.py          Phase 3: the yes/no questions, drafted messages, the answer log
    views.py                Phase 3: what each page shows (plain data, testable)
    charts.py               Phase 3: two Plotly charts
  run_analysis.py           runs Phase 2 and writes outputs/
  outputs/                  answers.json, analysis_report.md, *.csv reports
  tests/                    137 tests (engine, analysis, partial uploads, app); tests/fake_streamlit.py
                            runs app.py without a browser; tests/tools/check_streamlit_api.py
  schema_report.md          what is in each file and what is wrong with it
  assumptions.md            questions and the assumptions taken instead
```

## Windows PowerShell

Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH"). Then run these commands from
the `axeom-pulse` folder:

```powershell
# 1. create and activate a virtual environment (once)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run this once and try again:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# 2. install the libraries (once)
python -m pip install -r requirements.txt

# 3. run the tests (about 1.5 minutes)
python -m pytest -q

# 4. answer the six questions (writes outputs/)
python run_analysis.py

# 5. open the owner's app in your browser (http://localhost:8501)
streamlit run app.py
```

## Using the engine from Python

```python
from axeom import DataStore, build_model

store, reports = DataStore.from_folder("data/original")
model = build_model(store.raw, "data/match_decisions.csv")

model.resolution.clients      # one row per identity, with all names used
model.resolution.links        # every record -> client, method, confidence, file, line
model.resolution.review       # matches a human must decide
model.quality                 # every data problem, with file, line and record id

reports = store.ingest([("new_payments.csv", open("new_payments.csv", "rb").read())])
model = build_model(store.raw, "data/match_decisions.csv")   # recalculate everything
```

## Recording a decision from the review list

Add a row to `data/match_decisions.csv`:

```
value_type,value,target_email,decision,note
handle,@juniperwealthpartntx,vramirez@juniperwp.com,same,Victor confirmed this is their account
name,pecan,trent@pecanathletics.com,different,two separate gyms
```

`value_type` is `name`, `email`, `phone` or `handle`. `decision` is `same` or `different`. Decisions are
keyed by email, so they survive re-uploads.

## How each answer is computed (Phase 2)

All dates are measured from **2026-10-06** (`config.REFERENCE_DATE`), never the computer clock. Every figure is
written to `outputs/evidence_ledger.csv` as **confirmed** (read directly from a record, e.g. a payment that
quotes the invoice number), **inferred** (a rule joined records, e.g. a payment matched by client + amount +
date), or **unresolved** (the data cannot decide). The rules themselves are listed in `assumptions.md` (22-44).

| question | module | method in one line |
|---|---|---|
| Q1 real clients | `client_analysis.py` | identity groups from the engine; a "client" has bookings, invoices or videos; a payer for others (Kindred) is listed separately |
| Q2 money | `financial_analysis.py` | payments allocated once each (invoice ref > month in reference > client + amount + date > partial text); invoice balance = amount - allocated; bookings linked to invoices to find unbilled work; retainer months cover 0-price retainer shoots |
| Q3 lost requests | `communication_analysis.py` | each inbound edit request is matched to a video and to the board note; lost only when the video is identified, delivered and lacks the change |
| Q4 churn | `churn_analysis.py` | written checklist of signals with fixed points (docstring); not a predictive model |
| Q5 channels | `marketing_analysis.py` | first-touch lead source -> client -> invoiced / collected / estimated delivery cost; no spend data, so not profit |
| Q6 Monday | `owner_priorities.py` | three actions built from Q2-Q4 results, ranked by deadline and money at stake |

No message is ever sent: reminders and calls are listed for the owner to approve.

## Phase 2 outputs (`outputs/`)

| file | what it is |
|---|---|
| `answers.json` | the challenge template, filled. Same keys and order; links are left empty because they do not exist yet |
| `analysis_report.md` | the six answers with tables, evidence and limitations (generated) |
| `evidence_ledger.csv` | one row per (finding, supporting record): status confirmed / inferred / unresolved, calculation, file, line |
| `financial_reconciliation.csv` | every invoice with its true status, every payment allocation with method and confidence, plus overdue, we owe, never billed, tool errors and cost estimates |
| `client_identity_report.csv` | 30 clients, the payer, and identity questions; `client_record_links.csv` has every record -> client |
| `lost_requests_report.csv` | every edit request: completed / delayed / confirmed lost / unmatched, with the reason |
| `churn_analysis.csv` | risk points per client; `churn_signals.csv` has every signal and its record |
| `marketing_analysis.csv` | per channel: leads, conversion, invoiced, collected, estimated margin; `marketing_client_value.csv` per client |

`python run_analysis.py other_folder other_out` runs the same analysis on any folder of pack-format files.

## The owner's app (Phase 3)

| page | what the owner sees |
|---|---|
| **Today** | "Do this first" (one card, answerable on the spot), two "then" cards, and "Waiting for your OK": the next 5 yes/no questions. Everything else is one tap away, by topic |
| **Clients** | pick a client: names used, emails, phones, money (invoiced, received, open, overdue), risk, every lead, booking, invoice, payment, video, message and edit request, and how each record was linked. Also the "are these the same client?" questions |
| **Money** | overdue, never billed, we owe, not due yet; reminders and corrections to approve; where the billing tool is wrong |
| **Edits** | why requests get lost, requests per month (lost in orange), edits that can still be fixed before delivery, delivered videos to re-do |
| **Leads** | leads and referrals nobody answered, clients asking about more work, channels compared |
| **Add files** | upload, see per file what was inserted / updated / skipped / rejected and which headline numbers changed; undo the last upload or go back to the original pack; download answers.json, the evidence ledger and the owner's answers |

**Suggest** Each suggestion asks a question ("INV-1094 from Stonebridge Realty Group is 42 days late
(1,500). Send a reminder?") with Yes / No / Later (or Refund / Keep as credit / No, or Same client / Different).
"Yes" saves the answer and shows a drafted email, which the owner can copy or open in his own email app
(a `mailto:` link). The app has no way to send anything: no mail or HTTP library is imported, and a test
checks that. Answers can be undone.

**New files.** Uploads are saved as they are in `data/state/uploads/` and replayed on top of the original
pack through the Phase 1 engine, so the same file twice changes nothing, a record with a known id is updated,
and every change is in the history. A file the engine cannot read is not kept. If a saved upload ever made the
rebuild fail, the app offers to remove it. The state folder can be moved with `AXEOM_STATE_DIR`.


