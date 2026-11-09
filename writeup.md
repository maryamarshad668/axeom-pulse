# Axeom Pulse — Project Write-up

**Challenge:** Axeom — Pack 208  
**Project:** Axeom Pulse

- **Live application:** https://axeom-pulse.streamlit.app/
- **Source code:** https://github.com/maryamarshad668/axeom-pulse

## 1. Problem

Business owners often have important operational information spread across client records, invoices, payment records, edit requests, and marketing data. Reviewing these files manually makes it difficult to identify financial exposure, understand client activity, and decide what needs attention.

Axeom Pulse converts the supplied challenge data into a single owner-facing dashboard, helping an owner move from raw records to actionable business insights.

## 2. Solution

Axeom Pulse is a Python application with a Streamlit dashboard. It is organised into three main stages:

1. **Data engine:** Loads and processes the supplied files, standardises the data, and prepares records for analysis.
2. **Business intelligence engine:** Analyses client, financial, edit-request, and lead/marketing information and produces structured outputs.
3. **Owner dashboard:** Presents the results in practical views for reviewing business activity and identifying follow-up actions.

The dashboard includes Today, Clients, Money, Edits, Leads, and Add files views. It also provides downloadable outputs, including `answers.json` and the evidence ledger. Users can add files and rebuild the analysis, review changes, and undo or reset workspace decisions.

## 3. Key findings

The generated analysis reports:

- 30 clients in the analysed client set.
- $9,800 overdue in the payment analysis.
- $975 owed to clients.
- $7,025 potentially never billed.
- 39 lost edit requests identified for follow-up.
- Juniper Wealth Partners highlighted as the highest churn-risk client.
- Referral identified as the strongest channel by estimated delivery margin.

These findings are generated from the supplied challenge data and should be interpreted alongside the evidence ledger and assumptions. They are not independently audited financial statements.

## 4. Engineering, testing, and deployment

The application uses Python, Streamlit, pandas, RapidFuzz, and Plotly. The project includes automated tests for the analysis pipeline and dashboard behaviour.

The full test suite completed with **136 passed and 1 skipped**. The run also produced pandas FutureWarnings, but these were warnings rather than test failures.

The source code is available on GitHub, and the dashboard is deployed on Streamlit Community Cloud. The deployed application was checked and confirmed to load without requiring a login.

## 5. Assumptions and limitations

- The analysis is based on the files supplied in Pack 208. Results depend on the completeness and accuracy of those records.
- Some matching and categorisation decisions may require human review. The evidence ledger helps distinguish confirmed, inferred, and unresolved information.
- The churn-risk indicator is a rule-based checklist, not a trained predictive model.
- The marketing analysis does not have actual advertising-spend data. Estimated delivery margin must not be interpreted as net profit or measured marketing ROI.
- Records marked as potentially never billed are candidates for investigation, not confirmed lost revenue.
- The hosted workspace uses local file storage. On a free Streamlit Community Cloud instance, uploaded files and owner decisions may not persist after an app restart.

## 6. Links

- **Live dashboard:** https://axeom-pulse.streamlit.app/
- **GitHub repository:** https://github.com/maryamarshad668/axeom-pulse