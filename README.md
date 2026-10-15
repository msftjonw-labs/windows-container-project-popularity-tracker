# windows-container-project-popularity-tracker

## Workflow setup

Set the repository variable `APP_CLIENT_ID` and secret `APP_PRIVATE_KEY` for a
GitHub App. Set the repository variable `APP_INSTALLATION_OWNER` to the user or
organization login where that app is installed (not the app's name or ID).
If omitted, it defaults to the repository owner. The workflow selects that
account's installation, with access to all repositories granted to that
installation, rather than requiring this tracker repository to be selected.

If token creation returns `404 Not Found` from
`/users/msftjonw-labs/installation`, the configured app has no installation
accessible on `msftjonw-labs`. Install the app on that account, or set
`APP_INSTALLATION_OWNER` to the account where it is already installed. Check
the app's **Install App** settings and ensure `APP_CLIENT_ID` and
`APP_PRIVATE_KEY` belong to that same app; registering an app alone does not
install it. Re-run the workflow after correcting the installation or settings.

For project sync, configure `GH_PROJECT_ID` and `GH_SCORE_FIELD_ID` as repository
variables and select the installation on the account that owns the target
project, granting the app Projects read/write permission.
