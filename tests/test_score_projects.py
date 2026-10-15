import unittest
from unittest.mock import Mock, patch

from scripts import score_projects as tracker


def project_page(nodes, has_next=False, cursor=None):
    return {"node": {
        "field": {"id": "score", "dataType": "NUMBER"},
        "items": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        },
    }}


def metrics(title, stars):
    return {"repository": {
        "nameWithOwner": title,
        "stargazerCount": stars,
        "forkCount": 0,
        "mergedPRs": {"totalCount": 1},
        "closedPRs": {"totalCount": 0},
        "defaultBranchRef": None,
    }}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.multiple(
            tracker, GITHUB_TOKEN="test", PROJECT_ID="project", SCORE_FIELD_ID="score"
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)

    @patch.object(tracker, "run_graphql")
    def test_project_items_are_paginated(self, graphql):
        graphql.side_effect = [
            project_page([{"id": "a", "content": {"title": "org/a"}}], True, "next"),
            project_page([{"id": "b", "content": {"title": "org/b"}}]),
        ]
        self.assertEqual(tracker.get_project_items(), {"org/a": "a", "org/b": "b"})
        self.assertEqual(graphql.call_args.args[1]["cursor"], "next")

    @patch.object(tracker, "run_graphql")
    def test_duplicate_project_drafts_keep_first_item(self, graphql):
        for across_pages in (False, True):
            with self.subTest(across_pages=across_pages):
                first = {"id": "first", "content": {"title": "microsoft/WSL"}}
                remaining = [
                    {"id": "duplicate", "content": {"title": "microsoft/WSL"}},
                    {"id": "other", "content": {"title": "org/other"}},
                    {"id": "issue", "content": {}},
                    {"id": "redacted", "content": None},
                ]
                graphql.side_effect = (
                    [project_page([first], True, "next"), project_page(remaining)]
                    if across_pages else [project_page([first] + remaining)]
                )
                with patch("builtins.print") as output:
                    self.assertEqual(tracker.get_project_items(), {
                        "microsoft/wsl": "first", "org/other": "other",
                    })
                output.assert_called_once_with(
                    "Warning: Duplicate project draft title: microsoft/wsl; "
                    "reusing first item first, ignoring item duplicate."
                )
                if across_pages:
                    self.assertEqual(graphql.call_args.args[1]["cursor"], "next")

    @patch.object(tracker, "run_graphql")
    def test_text_score_field_is_rejected(self, graphql):
        data = project_page([])
        data["node"]["field"]["dataType"] = "TEXT"
        graphql.return_value = data
        with self.assertRaisesRegex(ValueError, "numeric"):
            tracker.get_project_items()

    @patch.object(tracker, "run_graphql")
    def test_archived_duplicates_are_ignored_and_titles_normalized(self, graphql):
        graphql.return_value = project_page([
            {"id": "active", "content": {"title": "Azure/AKS"}},
            {"id": "old", "isArchived": True, "content": {"title": "azure/aks"}},
        ])
        self.assertEqual(tracker.get_project_items(), {"azure/aks": "active"})

    @patch.object(tracker, "run_graphql")
    def test_active_duplicates_report_both_ids(self, graphql):
        for across_pages in (False, True):
            with self.subTest(across_pages=across_pages):
                first = {"id": "first", "content": {"title": "Azure/AKS"}}
                second = {"id": "second", "content": {"title": "azure/aks"}}
                graphql.side_effect = (
                    [project_page([first], True, "next"), project_page([second])]
                    if across_pages else [project_page([first, second])]
                )
                with patch("builtins.print") as output:
                    self.assertEqual(tracker.get_project_items(), {"azure/aks": "first"})
                output.assert_called_once_with(
                    "Warning: Duplicate project draft title: azure/aks; "
                    "reusing first item first, ignoring item second."
                )

    @patch.object(tracker, "run_graphql")
    def test_missing_item_is_refreshed_before_creation(self, graphql):
        graphql.side_effect = [
            project_page([]),
            metrics("Azure/AKS", 100),
            project_page([{"id": "existing", "content": {"title": "azure/aks"}}]),
            {},
            {},
        ]
        with patch.object(tracker, "TARGET_REPOSITORIES", [{"owner": "Azure", "name": "AKS"}]):
            tracker.main()
        self.assertFalse(any(
            call.args[0] == tracker.ADD_PROJECT_ITEM_MUTATION
            for call in graphql.call_args_list
        ))
        update = graphql.call_args_list[3].args[1]
        self.assertEqual(update["itemId"], "existing")

    @patch.object(tracker, "run_graphql")
    def test_sync_reuses_items_writes_numbers_and_orders_descending(self, graphql):
        targets = [{"owner": "org", "name": name} for name in ("a", "b", "c")]

        def respond(query, variables):
            if query == tracker.PROJECT_QUERY:
                return project_page([
                    {"id": "existing", "content": {"title": "org/a"}},
                    {"id": "duplicate", "content": {"title": "org/a"}},
                    {"id": "wsl-first", "content": {"title": "microsoft/WSL"}},
                    {"id": "wsl-duplicate", "content": {"title": "microsoft/WSL"}},
                ])
            if query == tracker.REPO_METRICS_QUERY:
                name = variables["name"]
                return metrics(f"org/{name}", {"a": 100, "b": 1000, "c": 200}[name])
            if query == tracker.ADD_PROJECT_ITEM_MUTATION:
                return {"addProjectV2DraftIssue": {
                    "projectItem": {"id": variables["title"]},
                }}
            return {}

        graphql.side_effect = respond
        with patch.object(tracker, "TARGET_REPOSITORIES", targets):
            tracker.main()

        calls = graphql.call_args_list
        creates = [c.args[1] for c in calls if c.args[0] == tracker.ADD_PROJECT_ITEM_MUTATION]
        self.assertEqual([c["title"] for c in creates], ["org/b", "org/c"])
        updates = [c.args[1] for c in calls if c.args[0] == tracker.UPDATE_FIELD_MUTATION]
        self.assertEqual([c["itemId"] for c in updates], ["existing", "org/b", "org/c"])
        self.assertEqual([c["value"] for c in updates], [5.0, 50.0, 10.0])
        self.assertTrue(all(isinstance(c["value"], float) for c in updates))
        moves = [c.args[1] for c in calls if c.args[0] == tracker.UPDATE_POSITION_MUTATION]
        self.assertEqual([(c["itemId"], c["afterId"]) for c in moves], [
            ("org/b", None), ("org/c", "org/b"), ("existing", "org/c"),
        ])

    @patch.object(tracker, "run_graphql")
    def test_repository_failure_fails_run(self, graphql):
        graphql.side_effect = [project_page([]), RuntimeError("API error")]
        with patch.object(tracker, "TARGET_REPOSITORIES", [{"owner": "org", "name": "a"}]):
            with self.assertRaisesRegex(RuntimeError, "org/a"):
                tracker.main()

    def test_partial_configuration_is_rejected(self):
        with patch.object(tracker, "SCORE_FIELD_ID", None):
            with self.assertRaisesRegex(ValueError, "both"):
                tracker.main()

    @patch.object(tracker.requests, "post")
    def test_graphql_errors_are_not_treated_as_success(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"errors": [{"message": "denied"}]}
        with self.assertRaisesRegex(RuntimeError, "denied"):
            tracker.run_graphql("query {}", {})
        self.assertEqual(post.call_args.kwargs["timeout"], 60)

    def test_mutation_uses_numeric_field_and_metrics_use_valid_arguments(self):
        self.assertIn("number: $value", tracker.UPDATE_FIELD_MUTATION)
        self.assertIn("$value: Float!", tracker.UPDATE_FIELD_MUTATION)
        self.assertNotIn("privacy:", tracker.REPO_METRICS_QUERY)


if __name__ == "__main__":
    unittest.main()
