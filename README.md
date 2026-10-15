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
