# Schema report: Axeom Challenge Pack 208

Reference date: **2026-10-06** . All figures below were produced by running the code in this
project against the original files. 

---

## 1. Datasets

Every file is UTF-8 without a BOM and has no blank lines. No record ID is duplicated within its file.
All values are read as text first; numbers and dates are parsed into separate columns, so the raw value is
always kept.

### bookings.csv — 173 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| booking_id | id (text) | 0 | 173 | `B001`, `B002`, `B003` |
| date | date with slashes | 0 | 91 | `04/01/2026`, `04/07/2026`, `04/08/2026` |
| client_name | client name (free text) | 0 | 82 | `Silverleaf DFW`, `Aisha Chen`, `Andre Chen` |
| email | email | 0 | 34 | `media@silverleafdfw.org`, `aisha@calmandcollected.fm`, `andre@ntxfounders.fm` |
| service | category | 0 | 5 | `Podcast Studio 2hr`, `Podcast Studio 4hr`, `Content Day` |
| status | category | 0 | 3 | `completed`, `cancelled`, `upcoming` |
| price | money as text (`$1,500`, `300`) | 0 | 12 | `$300`, `300`, `$550` |

### costs.csv — 2 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| item | text | 0 | 2 | `Studio time`, `Editor time` |
| cost | rate as text | 0 | 2 | `$40/hour`, `see team.csv` |
| note | free text | 0 | 2 | `Content Day uses 6 hours, Reels Pack 3, Retainer Shoot 4, podcast sessions their booked hours`, `editor_hours on the production board include revisions` |

### invoices.csv — 155 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| invoice_no | id (text) | 0 | 155 | `INV-1001`, `INV-1002`, `INV-1003` |
| bill_to | client name (free text) | 0 | 71 | `Juniper WP`, `Silverleaf Media Team`, `Aisha Chen` |
| email | email | 0 | 30 | `vramirez@juniperwp.com`, `media@silverleafdfw.org`, `aisha@calmandcollected.fm` |
| issued | date ISO | 0 | 96 | `2026-04-01`, `2026-04-02`, `2026-04-07` |
| due | date ISO | 0 | 96 | `2026-04-15`, `2026-04-16`, `2026-04-21` |
| amount | money `0000.00` | 0 | 7 | `2500.00`, `300.00`, `550.00` |
| status | category | 0 | 4 | `paid`, `unpaid`, `partial` |
| memo | free text (sometimes contains a booking id) | 41 | 99 | `Monthly retainer April`, `Podcast Studio 2hr 4/7`, `B004 Podcast Studio 2hr` |

### leads.csv — 99 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| lead_id | id (text) | 0 | 99 | `L001`, `L002`, `L003` |
| created | date-time ISO `YYYY-MM-DD HH:MM` | 0 | 99 | `2026-03-25 19:05`, `2026-04-01 19:29`, `2026-04-05 11:45` |
| name | person name | 0 | 93 | `Nolan Ibarra`, `Andre Chen`, `Marcus Foster` |
| business | business name | 12 | 87 | `Silverleaf Church`, `North Texas Founders`, `Foster Chiropractic` |
| email | email | 0 | 98 | `media@silverleafdfw.org`, `andre@ntxfounders.fm`, `marcus@fosterchiropractic.com` |
| phone | phone `NNN-NNN-NNNN` | 4 | 92 | `972-555-0186`, `817-555-0141`, `469-555-0195` |
| source | category | 0 | 6 | `Google`, `Meta ad`, `Website form` |
| first_reply | date ISO | 20 | 73 | `2026-03-26`, `2026-04-01`, `2026-04-07` |
| status | category | 0 | 4 | `Won`, `Lost`, `New` |

### messages.txt — 193 records

Free-text log, one message per line. Parsed fields: timestamp, channel, direction, counterpart (email / phone + first name / Instagram handle / other), voice-note duration, text.

| channel | direction | count |
|---|---|---|
| EMAIL | inbound | 78 |
| EMAIL | outbound | 1 |
| INSTAGRAM DM | inbound | 6 |
| INSTAGRAM DM | outbound | 1 |
| VOICE NOTE | inbound | 31 |
| WEBSITE FORM | inbound | 2 |
| WHATSAPP | inbound | 73 |
| WHATSAPP | outbound | 1 |

### payments.csv — 131 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| payment_id | id (text) | 0 | 131 | `P5001`, `P5002`, `P5003` |
| date | date with slashes | 0 | 92 | `05/04/2026`, `09/04/2026`, `10/04/2026` |
| payer | payer name (free text, often UPPER CASE) | 0 | 88 | `Silverleaf Media Team`, `AISHA CHEN`, `JUNIPER WEALTH PARTNERS` |
| payer_email | email | 2 | 29 | `media@silverleafdfw.org`, `aisha@calmandcollected.fm`, `vramirez@juniperwp.com` |
| amount | money `0000.00` | 0 | 9 | `300.00`, `2500.00`, `1500.00` |
| method | category | 0 | 3 | `Zelle`, `card`, `ACH` |
| reference | invoice number OR free text OR blank | 41 | 90 | `INV-1002`, `INV-1001`, `INV-1009` |

### production_board.csv — 348 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| video_id | id (text) | 0 | 348 | `V001`, `V002`, `V003` |
| client | client name (free text) | 0 | 95 | `Alana Hale DDS`, `Cedar Builders`, `Cedar Builders LLC` |
| title | text | 0 | 100 | `Why floss matters`, `Meet the doctor`, `Implants explained` |
| editor | team member name | 0 | 4 | `Ali`, `Rita`, `Nico` |
| stage | category | 0 | 4 | `Delivered`, `In revision`, `In review` |
| notes_on_board | free text | 273 | 17 | `Captions bigger`, `Brand color forest green`, `Remove prices part` |
| revisions | integer | 0 | 4 | `0`, `1`, `2` |
| editor_hours | decimal | 0 | 52 | `3.9`, `3.6`, `4.6` |
| client_approved_on | date ISO | 169 | 108 | `2026-05-06`, `2026-04-27`, `2026-05-02` |
| shoot_booking | booking id or blank | 73 | 102 | `B005`, `B006`, `B007` |

### team.csv — 5 records

| column | type / meaning | blank | distinct | examples |
|---|---|---|---|---|
| name | person name | 0 | 5 | `Zaid`, `Ali`, `Rita` |
| role | category | 0 | 3 | `Owner`, `Editor`, `Editor (freelance)` |
| joined | date ISO | 0 | 5 | `2023-01-01`, `2024-03-01`, `2025-01-15` |
| pay | rate as text (`$35/hour`) | 1 | 5 | `$35/hour`, `$30/hour`, `$22/hour` |

---

## 2. How the files relate

```
leads ──(email, phone)──┐
bookings ──(email)──────┤
invoices ──(email)──────┼──►  CLIENT  (resolved identity, see section 4)
payments ──(payer_email)┤
messages ──(email, phone, Instagram handle)
production ──(shoot_booking ─► bookings.booking_id, else client name only)

invoices.memo      ─► bookings.booking_id   (only sometimes: "B004 Podcast Studio 2hr")
payments.reference ─► invoices.invoice_no   (only sometimes; else free text or blank)
production.editor  ─► team.name
costs              ─► service hours (Content Day 6h, Reels Pack 3h, Retainer Shoot 4h, podcast = booked hours)
```

There is **no client ID anywhere**. A client can only be recognised by email, phone or name, and those
disagree between files (section 3).

| link | how reliable it is in Pack 208 |
|---|---|
| production → booking (`shoot_booking`) | 275 of 348 videos cite a booking. Every cited booking exists. The other 73 have only a name. |
| payment → invoice (`reference`) | 84 payments cite an invoice number that exists. 41 are blank and 6 are free text (e.g. `Kindred Jul invoices`, `half now half later`). |
| invoice → booking (`memo`) | 59 memos contain a booking ID, all of which exist. The rest have no memo or describe the service and date in words (`Podcast Studio 2hr 4/7`). |
| invoice ↔ payment by amount | **not used as evidence.** Many invoices share the same amount (54 invoices of 900.00), so an equal amount proves nothing. |

---

## 3. Data quality problems found

### 3.1 Names and identity
* **The same client under many spellings.** Examples: Juniper WP / Juniper Wealth / Juniper Wealth Partners /
  JUNIPER WEALTH PARTNERS. Also Calm & Collected / Calm and Collected / C&C Podcast / Aisha Chen. Also Apex /
  Apex Medspa / Apex Med Spa - Frisco / Apex Med Spa - Plano. Payment `payer` names are often in UPPER CASE.
* **Different people with the same name.**
  - *Alana Hale* is the contact of **Hale Dental** (hello@haledental.com) and also the contact of **Kindred
    Creative**, an agency (alana@kindredmktg.com).
  - *Sofia Khan* runs **Lakeside Roasters** and also **Khan Insurance**, with different emails.
  - Several contacts share first names (two Jareds, two Danas, two Jens, two Kelseys, two Nolans). The WhatsApp
    sender "Jared" on 214-555-0109 is **Jared Walsh of Walsh Law**, not Jared Khan of the roofing company.
* **Similar names that may or may not be one client.** *Stone Bridge Realty* (nadia@stonebridgerealty.com)
  and *Stonebridge Realty Group* / *Stonebridge RG* (nolan@stonebridgerg.com) have different contacts and
  domains. *Pecan Gym* (trent@pecangym.co) and *Pecan Athletics* (trent@pecanathletics.com) share one owner
  and one phone, and both are also called just "Pecan".
* **One person, two emails.** Aisha Chen uses aisha@calmandcollected.fm and aisha.chen.mail@gmail.com (same
  phone). Brooke Becker uses brooke@ and frisco@apexmedspa.com (same phone). Juniper has two contacts on one
  domain.
* **An agency paying and booking for its customers.** Kindred Creative asked by email to be billed for *Sunset
  Pediatrics* and *Luna Physical Therapy*. It pays with free-text references (`Kindred Jun invoices`, …). Some
  bookings for those customers are under Kindred's email (B144, B172).
* **Typo in an email.** Booking B085 uses `hello@haledental.con`; every other record uses `.com`.
* **Duplicate lead.** L007 and L042 are both Alana Hale / Hale Dental with the same email and phone.
* **Junk leads and messages.** Examples: `test@test.com`, *Crypto Deals*, *SEO Services*, *Free followers*, a
  fake "Instagram support" DM, `noreply@domain-renewals.net` and `billing@acct-dept.co`. These are kept (never
  deleted) as their own lead-only or message-only identities, so they never merge into a real client.

### 3.2 Dates
* **Two different slash formats.** `bookings.date` is month-first (`04/13/2026`, no first part above 12).
  `payments.date` is day-first (`24/04/2026`, no second part above 12). Taken one by one, 77 booking dates and 53
  payment dates could be read either way. Each file settles its own order, because the same column also contains
  dates that can only be read one way.
* **A date after the reference day.** Lead L096 has `first_reply` = 2026-10-07, one day after "today".
* **A stale status.** Booking B157 is still `upcoming`, but its date (Oct 6) is the reference day itself. It is
  not flagged, because it may still happen today.
* **Delivered without approval.** 139 videos are `Delivered` with no `client_approved_on`. One video (V111) has
  an approval date but is `In revision`.

### 3.3 Money: invoices vs payments vs bookings
* **The billing tool contradicts the bank file:**
  - INV-1082 (1,500.00) is `unpaid`, but payment P5077 references it.
  - INV-1134 (225.00) is `unpaid`, but payment P5124 references it.
  - INV-1080 (900.00) is referenced by two payments, P5072 and P5076, totalling 1,800.00. This may be a double
    payment.
* **48 invoices are `paid`, but no payment names them.** The bank file cannot confirm them. Many probably match
  payments with blank references, but an equal amount is not proof, so they are flagged as *unverified*, not
  fixed.
* **41 payments have no reference and 6 have free text.** They cannot be tied to an invoice automatically.
* **Unusual payment amounts.** P5080 and P5103 (1,800.00, Kindred) and P5114 (450.00, "half now half later")
  do not equal any single invoice amount.
* **A void duplicate.** INV-1155 (void) duplicates INV-1065 (paid): same email, date, amount and memo.
* **A cancelled booking was invoiced.** INV-1137's memo references B148, which is `cancelled`.
* **Prices.** 22 *Retainer Shoot* bookings have price 0 (retainers seem to be billed monthly at 2,500.00 or
  2,000.00). 18 *Podcast Studio 2hr* bookings cost 225 instead of 300; a message on 2026-05-26 offers Silverleaf
  25% off from June. `price` is written both with and without `$`.
* **Booked but maybe never billed.** Most invoices do not cite a booking ID, so "booked but not billed" cannot be
  decided by ID alone. Phase 2 will need the matching rules in `assumptions.md`. The engine does not treat a
  booking as billed without evidence.

### 3.4 Messages
* 193 messages in 5 channels: EMAIL 79, WHATSAPP 74, VOICE NOTE 31, INSTAGRAM DM 7, WEBSITE FORM 2. Three are
  outbound, including one broadcast "to all clients".
* No message has an ID. Senders are identified by email, by a phone number plus first name, or by an Instagram
  handle. One unknown number (214-555-0121) signs only "Brooke".
* Edit requests ("make the captions bigger…") often name the video by its title. They can be checked against
  `production_board.notes_on_board`, but that comparison is Phase 2 work.

### 3.5 Quality report on the original pack (counts)

| severity | issue type | count |
|---|---|---|
| high | possible_client_duplicate (review items) | 7 |
| high | conflicting_record (billing tool vs bank) | 3 |
| high | identity_conflict (agency booking under its own email) | 2 |
| medium | payment_without_reference | 41 |
| medium | unstructured_reference | 6 |
| medium | identity_review (review items) | 4 |
| medium | conflicting_record (cancelled booking invoiced, approval vs stage) | 2 |
| medium | missing_identifier (payments with no email) | 2 |
| medium | possible_duplicate_record (INV-1065 / INV-1155) | 2 |
| medium | date_after_reference_date | 1 |
| medium | suspicious_email | 1 |
| low | missing_value (delivered without approval date) | 139 |
| low | unverified_status (paid, no bank evidence) | 48 |
| low | suspicious_amount (price 0, discounted price, odd payment) | 43 |
| low | possible_duplicate_record (duplicate lead) | 2 |
| low | unmatched_client (two Instagram messages) | 2 |

Every issue row names its source file, line number and record ID (see `build_quality_report`).

---

## 4. Client matching strategy

Every record that names a party becomes a *mention*. Mentions are grouped in this order:

1. **Exact email** (after lower-casing and trimming).
2. **Exact phone** (reduced to 10 US digits). The two groups are joined only if they do not contradict each
   other: either they share an email domain, or neither has a domain of its own and the business names are
   similar. Example: Aisha's Gmail joins Calm & Collected through her phone. Pecan Gym and Pecan Athletics share
   a phone but have different domains, so they are **not** joined; they go to review.
3. **Confirmed alias.** These are decisions the owner records in `data/match_decisions.csv` (`same` or
   `different`), keyed by email so they survive rebuilds.
4. **Exact normalised name.** Normalising removes case, punctuation, apostrophes, `&`→`and`, a leading
   "The/Dr./Pastor", and trailing "LLC/PLLC/Inc/DDS". Two groups with their own contact details join on a shared
   name only when the domains do not contradict. That is how `hello@haledental.con` joins `.com`: same name, same
   domain stem. A first name alone is never used.
   For records with no contact details at all, names are searched in tiers:
   - first, how a client describes itself on a lead form;
   - then, business names typed elsewhere;
   - then, person names.
5. **Fuzzy name (RapidFuzz).** The score is the best of `token_sort_ratio` and a no-spaces `ratio`
   (`token_set_ratio` is deliberately not used, because "Hale" would match "Hale Dental" at 100). A record is
   linked automatically only at ≥ 92 **and** at least 5 points above the next client. A score of 80–91 or a
   close runner-up goes to review. Below 80 the record stays unmatched.

Extra rules:

* A **video that cites its shoot booking** inherits that booking's client. 275 of 348 videos link this way.
* When a record's email points to one client but its name is how *another* client describes itself (Kindred
  booking for Sunset Pediatrics), the email link is kept and the record is **flagged for review**.
* **Possible duplicate clients** (separate clients with very similar names) are listed, never merged. Their
  comparison ignores industry words such as "dental", "tax" and "services", so *Vega Tax Services* vs *Rivera
  Tax Services* is not flagged but *Stone Bridge Realty* vs *Stonebridge Realty Group* is.

Result on Pack 208:
* 98 identities: 32 with bookings, invoices or videos, 63 leads only, and 3 that appear only in messages.
* 1,098 mentions: 624 linked by email, 275 by booking, 113 by phone, 79 by exact name and 5 by fuzzy match
  (Instagram handles). 2 Instagram messages are left unmatched on purpose (one uncertain, one spam).
* **11 items for the owner to decide.** Defining *who counts as a real client* (for example, is Kindred a client
  or a payer?) is a business decision left for Phase 2.

---

## 5. New-upload strategy

* **File type:** decided by the **column header**. The file name is only a hint, so `bank_export_october.csv`
  with payment columns is accepted as payments. Unknown or incomplete files are rejected whole, with the missing
  columns named.
* **Merge key:** the stable ID: lead_id, booking_id, invoice_no, payment_id, video_id, team `name`, costs `item`.
  - New ID → **insert**.
  - Same ID, same content → **skip as duplicate**.
  - Same ID, different content → **update** (a correction). The old values are kept in the history log.
  - Same ID twice in one file with different content → **ambiguous, not applied**.
* **Messages:** have no ID, so the key is `message_key`, a SHA-1 of timestamp, channel, direction, sender and
  text after collapsing whitespace and case. Re-uploading the same log, even with Windows line endings or a
  BOM, changes nothing. Two genuinely identical messages in the same minute would collapse into one; that is
  accepted and documented.
* **Rejected rows:** a missing ID, or a money field that is text (`abc`). Blank amounts are kept as unknown
  (never 0) and reported.
* **Dates:** the day/month order is decided **per uploaded file**. A new file with no deciding evidence
  falls back to the pack's convention for that dataset, and every such date is flagged.
* **After each upload**, normalisation, identity resolution and the quality report are all rebuilt from the
  current data. Nothing is patched incrementally.
