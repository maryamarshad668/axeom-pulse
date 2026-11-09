# Axeom Pulse — Daily Development Log

**Challenge:** Axeom Internship Challenge — Pack 208  
**Project:** Axeom Pulse  
**Developer:** [Maryam Arshad]

## Entry 1 — Data and analysis implementation

**Date / time spent:** [Sunday night 5 hours]

**Completed**
- Built the data-processing and analysis stages for the supplied Pack 208 files.
- Produced structured outputs, including `answers.json`, an analysis report, and an evidence ledger.
- Added analysis for client information, money/payment status, edit requests, and lead/marketing performance.

**Next priority**

Complete the owner-facing dashboard and test the end-to-end workflow.

## Entry 2 — Dashboard and testing

**Date / time spent:** [Monday morning 3 hours]

**Completed**
- Implemented the Streamlit owner dashboard with Today, Clients, Money, Edits, Leads, and Add files views.
- Added file-upload/workspace functionality and downloads for key analysis outputs.
- Ran the full automated test suite.
- Test result: **136 passed, 1 skipped**.

**What went wrong, and how did I fix it?**

The `streamlit` command was not recognised in the terminal. I installed Streamlit in the active Python environment and launched the application using:

```powershell
python -m streamlit run app.py
```

**Next priority**

Publish the repository and deploy the dashboard.

## Entry 3 — Repository and deployment

**Date / time spent:** [Monday night 2 hours]

**Completed**
- Created the GitHub repository: https://github.com/maryamarshad668/axeom-pulse
- Committed and pushed the project files.
- Deployed the dashboard to Streamlit Community Cloud: https://axeom-pulse.streamlit.app/
- Checked the deployed app and confirmed that it loads without requiring a login.

