# windows-container-project-popularity-tracker

## Workflow setup

Set the repository variable `APP_CLIENT_ID` and secret `APP_PRIVATE_KEY` for a
GitHub App installed on the repository owner's account. The workflow explicitly
selects that owner's installation, with access to all repositories granted to
that installation, rather than requiring this tracker repository to be selected.
If token creation returns `404 Not Found`, verify that the configured app is
installed on that account; registering an app alone does not install it.

For project sync, configure `GH_PROJECT_ID` and `GH_SCORE_FIELD_ID` as repository
variables and grant the app access to the target project with Projects read/write
permission.

The score field must be a **Number** field named **Popularity Score**. The script
validates its ID and type before syncing. Each repository is represented by a
draft item titled `owner/repository`, with its URL in the body: GitHub Projects
cannot use a repository itself as an issue or pull-request item. Existing drafts
with the same title are updated rather than duplicated.
For configured target repositories, the script keeps the first active draft
returned by the project API and archives extra drafts with the same
case-insensitive title. Archiving preserves the extra drafts' data but removes
them from active views. Cleanup happens after pagination completes and before
scoring; archive failures stop the sync. Unrelated drafts are left untouched.

Workflow runs share a concurrency group so only one sync runs at a time. Do not
run local syncs concurrently with each other or the workflow: draft creation has
no atomic uniqueness constraint. Before creating a missing draft, the script
refreshes the project lookup and compares titles case-insensitively. Archived
items are ignored. Removing a target
does not automatically delete or archive its existing project items.

After writing numeric scores, the script moves the tracked items to the top in
descending score order (highest first). Keep the project view's sort set to
**Manual**, or select **Popularity Score → Descending** and save the view.
An explicit view sort overrides manual item positions. API failures fail the
workflow instead of reporting a successful sync.

Run the regression tests with `python -m unittest discover -s tests -v`.

## Success email

The final workflow step sends an email only after all scoring and project sync
steps succeed. Configure these GitHub Actions repository variables and secrets:

| Setting | Location | Meaning |
| --- | --- | --- |
| `SMTP_HOST` | Variable | SMTP server hostname; leave unset to skip email |
| `SMTP_PORT` | Variable | Optional port; defaults to 587 for STARTTLS or 465 for SSL |
| `SMTP_SECURITY` | Variable | `starttls` (default) or `ssl` (implicit TLS) |
| `SMTP_FROM` | Variable | Sender email address |
| `SMTP_TO` | Secret | Comma-separated recipient email addresses |
| `SMTP_USERNAME` | Secret | SMTP login username, if authentication is required |
| `SMTP_PASSWORD` | Secret | SMTP login password or provider app password |

Once `SMTP_HOST` is set, sender and recipients are required. Set both login
secrets or neither for a server that permits unauthenticated relay over TLS.
TLS and certificate verification are always required; plaintext SMTP is not
supported. These same settings can be supplied as environment variables when
running `python scripts/send_success_email.py` locally.

The email identifies the repository and links to the workflow run. SMTP errors
(including rejected recipients) fail the notification step and the workflow;
they do not undo the completed project sync. SMTP acceptance does not guarantee
inbox delivery. No failure email is sent for a failed or cancelled sync.

## Weekly dashboard

The workflow runs every Monday at **09:00 fixed PST (UTC-8)**, or **17:00 UTC**
year-round. This is 10:00 Pacific daylight time during daylight saving time.
GitHub may delay scheduled workflows; the cron is not an exact execution guarantee.

The dashboard has four custom fields:

| Field | Type | Meaning |
| --- | --- | --- |
| Popularity Score | Number | Latest computed score |
| WoW Delta | Number | This week's score minus the previous calendar week's score |
| WoW Trend | Single-select | Increase (green) for positive delta; Decrease (red) for negative delta |
| Ranking | Multi-select | Current and previous-week ranking labels |

Weeks start Monday at 00:00 fixed PST. Manual reruns in the same week update that
week's score but retain the same previous-week baseline. The most recent successful
per-repository snapshot in a week is used the following week. If the immediately
previous week has no snapshot, Delta and Trend are blank; an older score is not
substituted. Zero delta is displayed as 0, with no Trend label.

Snapshots are stored in a `popularity-weekly` HTML comment in each repository's
draft body, not in additional project fields or runner-local files. Existing body
text is preserved. Do not edit these comments: invalid snapshot data stops the sync
instead of resetting history. The first run initializes history; undated scores
already in the project cannot be treated as last week's scores.

Each successful project sync creates or reuses a **Ranking** multi-select field:

| Label | Color | Rule |
| --- | --- | --- |
| Top 3 | Green | First three repositories by descending score |
| Over 10,000 | Yellow | Score strictly greater than 10,000, outside the top three |
| Previous Week's Top 3 | Green | Top-three category in the previous calendar week |
| Previous Week's Over 10,000 | Yellow | Over-10,000 category in the previous calendar week |
| No label | Default | All remaining repositories |

Top-three status takes priority within each week, including when those scores
exceed 10,000. Ties are broken by repository name. Current and previous-week labels
can coexist, such as Top 3 plus Previous Week's Over 10,000. Labels are replaced on
every sync, not accumulated indefinitely. A repository outside this week's
categories can still have a previous-week label.

Metrics are collected for all targets before publishing dashboard values. Invalid
snapshot data is checked before any row is updated. GitHub has no multi-row
transaction: a failed API write can leave a partially updated dashboard; rerun to
complete it. Each row's snapshot is saved after its field updates, so retries
preserve the prior-week baseline.

The old single-select Ranking field is renamed Ranking (legacy) during migration.
It is removed only after new values are written and all views show the replacement.
A failed migration can be resumed by rerunning. This replaces the Ranking field ID;
Popularity Score and its configured ID are unchanged. Missing Ranking options are
added in place and incorrect required-option colors are repaired, preserving
existing option IDs, descriptions, and custom options.
After updating options, the script reads the field again before validating names,
colors, and IDs rather than relying on the mutation response. Persistent name or
color mismatches stop the sync and report the returned option names and colors.
Required options are reconciled to the documented colors. The sync makes all
four dashboard fields visible in existing project views while preserving other
visible columns.

In the project view, open the field menu, enable **Ranking**, and save the view.
GitHub Projects supports only one green label shade, not light/dark greens,
conditional row backgrounds, or bold repository titles. Top 3 uses green,
Over 10,000 uses yellow, and the remaining repositories have no ranking label.
