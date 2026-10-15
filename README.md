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
If duplicate draft titles already exist, the script warns and reuses the first
item returned by the project API. Other duplicates are left untouched and can be
cleaned up manually; they do not block evaluation or sync.

Workflow runs share a concurrency group so only one sync runs at a time. Do not
run local syncs concurrently with each other or the workflow: draft creation has
no atomic uniqueness constraint. Before creating a missing draft, the script
refreshes the project lookup and compares titles case-insensitively. Archived
items are ignored. If active duplicate drafts already exist, the script reports
their item IDs and stops; archive the extras before rerunning. Removing a target
does not automatically delete or archive its existing project items.

After writing numeric scores, the script moves the tracked items to the top in
descending score order (highest first). Keep the project view's sort set to
**Manual**, or select **Popularity Score → Descending** and save the view.
An explicit view sort overrides manual item positions. API failures fail the
workflow instead of reporting a successful sync.

Run the regression tests with `python -m unittest discover -s tests -v`.
