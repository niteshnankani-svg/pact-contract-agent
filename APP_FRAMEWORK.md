# App framework: Pact

## Company problem

Procurement and legal operations teams must identify which amendment controls a renewal notice period, reconcile it with internal policy, and update the operational deadline. A plausible explanation is insufficient: the task needs the correct source version, an accountable approver and an auditable write.

The application focuses on this measurable workflow rather than offering a general document chatbot. Existing enterprise contract workflows establish the use case: [Ironclad renewal automation](https://support.ironcladapp.com/hc/en-us/articles/34100183090455-Recipe-Use-Automated-Emails-to-Streamline-Your-Renewals-Process) and [procurement integration](https://support.ironcladapp.com/hc/en-us/articles/12278665732247-Coupa-Integration-Overview). These sources do not establish a quantified ROI for Pact.

## User and working surface

The review workspace contains a matter inbox, source documents, quoted findings, proposed tasks/redlines and an activity trail. An obligations view shows current or stale tasks. Reader, reviewer and admin API roles separate viewing, review decisions and playbook administration. Tokens stay in browser memory.

## Current executable scope

- Text, text-bearing PDF and DOCX inputs; explicit executed/draft status and amendment ancestry.
- Notice and renewal-date evidence drives deterministic calendar-day deadline arithmetic; explicit ISO and named-month dates include a required year.
- Liability, assignment and confidentiality are extracted for review. Narrow numeric/consent checks surface policy deviations and create evidence-linked review tasks. They do not determine legal enforceability or automatically approve exceptions.
- Notice policy generates a proposed clause replacement. Only an executed future source changes operative obligations.
- Approval creates or updates internal tasks; external task-system connectors are not implemented in this release.
- JSON review export and a new DOCX document containing tracked deletions/insertions.

## Acceptance criteria

An unapproved review creates no task. An approval must match the proposal hash and current source revision. Missing or unsupported evidence prevents execution. New documents or playbook changes invalidate previous reviews and tasks. Replaying an approved action does not duplicate its task. Restarting a worker preserves the approval pause. Cross-tenant requests cannot retrieve another tenant's matter.

## Dataset fit

| Dataset | What it supplies | What it does not supply |
|---|---|---|
| CUAD | Labeled source spans for contract clause extraction | A company's renewal workflow or internal playbook |
| ACORD | Lawyer-annotated clause retrieval relevance | End-to-end deadline correctness |
| ContractNLI original | NDA hypotheses with document evidence spans | General contract advice or renewal actions |

All three are downloadable from pinned revisions. Their attribution and licenses are recorded in `data/manifest.json`. Illustrative app contracts are synthetic and never counted as benchmark successes.

## Release boundary

The code is deployable with a separate worker and PostgreSQL. Production qualification still requires a stronger evaluated model, representative customer contracts, business-calendar/timezone rules where applicable, security/retention review, backup/restore practice and cloud-specific load validation. Containerization alone does not establish these properties.
