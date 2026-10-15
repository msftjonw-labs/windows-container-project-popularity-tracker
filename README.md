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
