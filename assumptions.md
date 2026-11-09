# Assumptions and open questions (Phase 1)

The brief says we cannot ask questions. Each item below is the question I would have asked, followed by what
the engine assumes for now. Anything marked **(Phase 2)** is a business decision the engine deliberately does
not make yet.

## Identity

1. **What is a "client": a business, a person, or whoever pays?**
   *Assumed:* a client is a business identity. People (contacts) and payers attach to it. Kindred Creative
   pays and books for other businesses; the engine keeps it as its own identity and flags the affected bookings
   instead of re-assigning them. Who counts as a real client is decided **(Phase 2)**.
2. **Are Pecan Gym and Pecan Athletics the same client (one owner, one phone, two domains)?**
   *Assumed:* not without confirmation. Listed for review.
3. **Are Stone Bridge Realty and Stonebridge Realty Group the same company?**
   *Assumed:* no. They have different contacts and domains. Listed as a possible duplicate.
4. **Are the Apex Med Spa Frisco and Plano locations one client?**
   *Assumed:* yes. Invoices for both locations go to brooke@apexmedspa.com, and the Frisco lead
   (frisco@apexmedspa.com) has Brooke's phone and the same email domain. The location suffix is kept as a name
   variant. Revisit if the owner bills the locations separately.
5. **Is an email typo a different client?**
   *Assumed:* no, when the local part and the domain stem match and the name is identical
   (`hello@haledental.con`). It is still reported as a suspicious email.
6. **Can a first name decide who sent a message?**
   *Assumed:* never. Only the email, phone or handle decides. An unknown number signed "Brooke" goes to review.
7. **Instagram handles** are matched fuzzily against business names and email domains. One handle
   (`@juniperwealthpartntx`, score 88) is close but below the auto-link bar, so it waits for a decision.
8. **Spam and test records** (test@test.com, "SEO Services", phishing DMs) are kept, never deleted. They do
   not become clients because they have no bookings or invoices.

## Dates

9. **Which order are slash dates in?**
   *Assumed:* the order each file proves itself. Bookings are month-first and payments are day-first in the pack.
   A new file that proves nothing uses the pack's order for that dataset, and every such date is flagged
   `assumed_source_convention`. A file mixing both orders leaves the ambiguous dates empty instead of guessing.
10. **Time zones:** timestamps have none. *Assumed:* all are Dallas local time.
11. **"Today"** is always 2026-10-06 (`config.REFERENCE_DATE`). The computer clock is never used in business
    logic.

## Money

12. **Does a payment with no invoice reference pay a specific invoice?**
    *Assumed:* unknown until a human or a stronger rule decides. The engine never matches on amount alone.
13. **Is an invoice marked `paid` actually paid?**
    *Assumed:* only the bank file (payments.csv) is evidence of money received. 48 `paid` invoices have no
    payment naming them; they are reported as unverified, not as unpaid.
14. **A blank amount** means unknown, never 0.
15. **Retainer Shoot bookings at price 0** are assumed to be covered by a monthly retainer invoice. They are
    flagged, and nothing is assumed billed.
16. **The 225.00 podcast price** is assumed to be the 25% discount promised to Silverleaf on 2026-05-26. This
    is still flagged as an unusual price.
17. **Billing a booking:** a booking is treated as billed only when there is evidence (a booking ID in the
    memo, or a rule confirmed in Phase 2 such as same client + service + date in the memo).

## Uploads

18. **Same ID with different content in a later file = a correction.** The new version wins, and the old one is
    kept in `history`.
19. **A record that disappears from a later upload is NOT deleted.** Uploads only add or correct. Deletions
    would need an explicit action **(Phase 2)**.
20. **Two identical messages in the same minute from the same sender** are treated as one message, because
    they cannot be told apart.
21. **Persistence on Streamlit Community Cloud:** its disk is temporary. In Phase 1, uploads live for the
    browser session. `DataStore.save()` / `load()` exist for local use. Where to keep data permanently for
    free is decided **(Phase 2)**.

## Questions for the owner 

* Is Kindred Creative your client, or only the payer for Sunset Pediatrics and Luna Physical Therapy?
* Are Pecan Gym and Pecan Athletics one account?
* Is Stonebridge Realty Group related to Stone Bridge Realty?
* Who texts from 214-555-0121 signing "Brooke"?
* Did Walsh Law pay INV-1080 twice? Were INV-1082 and INV-1134 paid?

---

# Phase 2 additions

## Clients
22. **Kindred Creative is a payer, not a client.** Kindred asked by email (2026-06-10) to be billed for Sunset
    Pediatrics and Luna Physical Therapy. Its invoice memos cite those clients' bookings, and every booking under
    its email carries their names. Those bookings, and the videos shot at them, are counted for Sunset and Luna.
    Kindred is listed under `payers_who_are_not_clients`.
23. **Pecan Gym and Pecan Athletics are one client.** The client wrote on 2026-08-01: "Pecan Gym is now Pecan
    Athletics! New email is trent@pecanathletics.com". Any message where a client announces its *own* new email
    or phone ("new email is", "our new office number is", "from now on", "is now") joins the old and new
    contact (`announced_alias`). This is how the WhatsApp from 214-555-0121 signed only "Brooke" is tied to Apex
    Med Spa. A number passed on for someone else ("his number is ... call him") is not an announcement.
23b. **Instagram handles** are compared with the client's email domain or business name after removing common
    suffixes (tx, dfw, co, official...). A prefix covering at least 80% of the name counts (score 95).
    `@instasupportteam` matches nothing; it looks like a phishing DM and is left unlinked.
24. **Stone Bridge Realty (Nadia Hassan) and Stonebridge Realty Group (Nolan Hassan) stay separate.** They have
    different contacts, domains and invoices, and nothing in the data says they are one company. This item is
    left open for the owner.
25. **Canonical name:** the most recent business name the client typed on a lead form. If that name only adds a
    location suffix ("Apex Med Spa - Frisco"), the shorter, earlier name is used. If the business field just
    repeats the person's name, the most common business spelling on bookings and invoices is used.

## Money
26. **Payments without an invoice number** are matched only within the same client (or a client its payer pays
    for). The invoice's open balance must equal the payment and the invoice must be issued before it. When
    several invoices fit, the one the billing tool already marks `paid` is preferred, then the closest date. These
    are *inferred*, never *confirmed*.
27. **A free-text reference naming a month** ("Kindred Jul invoices", "july sessions") covers that payer's
    invoices issued in that month, oldest first.
28. **INV-1099 (Jared's Roofing)** is treated as unpaid although the billing tool says `paid`. The client has 6
    invoices and 5 bank payments, and every payment is already matched to another invoice. This is *inferred*,
    and is shown separately from the confirmed overdue figure.
29. **A never-billed retainer month is valued at the client's usual retainer invoice** (2,500 for Walsh and
    Haddad). The May retainer shoots exist, but no May retainer invoice does, and no price was ever written down
    for them. This is *inferred*.
30. **Walsh Law's July retainer (2,000 vs 2,500)** is not called an error, because nothing explains it. It is
    listed as unresolved.
31. **A paid booking that was cancelled and moved.** If an invoice bills a cancelled booking that was paid, and
    the same client has a completed booking of the same service and price within 30 days after it, the payment
    is moved to the rescheduled shoot (and to the invoice billing it, if there is one). A client message within
    2 days saying cancel + move/reschedule raises confidence to high. This is *inferred*. For Stone Bridge Realty:
    B148 (paid via INV-1137 / P5122) was cancelled with "Can we move it? We already paid", and B155 is the
    rescheduled shoot. INV-1145 bills B155 again and the tool shows it unpaid, so INV-1145 is reported as a tool
    error (it is effectively paid) and nothing is owed either way. Only a paid cancellation with **no** later
    shoot is reported as a refund we owe (inferred).
32. **"Days late"** = 2026-10-06 minus the due date. An invoice due on 2026-10-06 is not late yet.
33. **Costs we incurred** (editor hours × rate, studio hours × $40) are estimates. With no payroll or bill
    records, they are *not* reported as money we owe.

## Requests, churn, marketing
34. **A request is "confirmed lost"** only when its video is identified and that video's board row does not
    carry the requested change after delivery. If the video cannot be identified with certainty, the request is
    "unmatched", never lost. If the video is still being edited, it is "delayed".
35. **The board has a single `notes_on_board` field per video.** A second request on the same video can only be
    recorded by replacing the first, so such requests count as lost.
36. **"Got worse in"** = the first month whose lost share is at least double the earlier rate and stays there.
    Months with fewer than 5 requests are too small to judge.
37. **Churn risk is a written checklist with fixed points, not a model.** A client who said it would pause and
    has booked nothing since (Maple Chiropractic) is "already lost", not "likely to leave".
38. **Marketing attribution is first touch:** the source of the client's earliest lead. Spam leads (no phone
    and no business) are excluded. With no ad or referral spend in the data, channels are compared on margin
    after delivery cost, which is not profit.
39. **"Monday morning"**: the brief says Monday, but 2026-10-06 is a Tuesday. Actions are planned for the
    week of 2026-10-06, and all dates are calculated from 2026-10-06.
40. **Booking-to-invoice links**, strongest first: booking id in the memo; service + date in the memo; a retainer
    memo naming a month covers that client's retainer bookings in that month; otherwise same client, same amount,
    invoice issued 7 days before to 14 days after the shoot (*inferred*, closest pair first, each used once).
41. **Videos without a shoot booking get an estimated date.** Video ids are issued in date order, so the date is
    interpolated from the nearest cards with a known shoot date. This is only used to decide which videos a
    message could be about (a message cannot be about a video made after it).
42. **Which video a request is about:** a video title in the message (quoted request wording is removed first, so
    "add the line 'walk-ins welcome'" is not read as the title "Walk-ins welcome"); the longest matching title
    wins. If a title repeats, the card already carrying the requested note is preferred. "The one we shot last
    week" means the client's latest shoot in the previous 21 days; if that shoot made several videos, a later
    follow-up naming one of them decides, otherwise the request is "unmatched".
43. **Standing instructions** ("always use the full business name") are checked against the client's videos made
    after the message. A later message such as "I asked for the full name. Not happy." is a complaint and a
    follow-up, not a new request.
44. **Partial or new uploads.** Every analysis runs on any subset of the files. A question that needs a missing
    file is left empty in answers.json and the reason is stated, rather than guessed (tested for each file
    alone, every file but one, and no files).

# Phase 3 additions (the owner's app)

45. **One shared set of data.** Uploads and answers are saved on the server and seen by everyone who opens
    the link (the owner is one person; a grader's upload should still be there when the page is reopened on a
    phone). Free hosting resets them when the app restarts; the original pack is always there. Nothing about
    who uploaded is recorded, because there is no login.
46. **"Today" stays 2026-10-06.** Days late and every business date use the data's date. Only the audit time of
    an upload or an answer uses the real clock (shown as UTC).
47. **What comes first** follows the Q6 rule: a client who set a deadline, then edits that can still be fixed
    before delivery, then money (we owe first, then the longest overdue, then unbilled work, then billing tool
    corrections), then leads, then delivered videos to re-do, then identity questions.
48. **Juniper's overdue invoice** is still listed, with a tip to raise it in the call rather than by email.
49. **"Different" on an identity question** only hides the question; the clients were already separate.
    **"Same client"** adds an email alias in the Phase 1 decision format and everything is recalculated.
50. **Suggestions that need no action are not shown**: the voided duplicate (INV-1155), and tool errors that
    are already covered by a refund question (INV-1080 paid twice, INV-1043 overcharged).
51. **A reminder draft never states a fact the data does not support**: it says what is open on our side and
    asks for the payment reference if the client already paid (useful for INV-1099, where the tool says paid).

