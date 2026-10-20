import unittest
import json
from datetime import date, datetime, timedelta, timezone
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
        ranking = patch.object(tracker, "update_rankings")
        self.rankings = ranking.start()
        self.addCleanup(ranking.stop)

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
                output.assert_not_called()
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
    def test_active_target_duplicates_are_archived_after_pagination(self, graphql):
        for across_pages in (False, True):
            with self.subTest(across_pages=across_pages):
                first = {"id": "first", "content": {"title": "Azure/AKS"}}
                second = {"id": "second", "content": {"title": "azure/aks"}}
                graphql.side_effect = (
                    [project_page([first], True, "next"), project_page([second]),
                     {"archiveProjectV2Item": {"item": {"id": "second", "isArchived": True}}}]
                    if across_pages else [project_page([first, second]),
                     {"archiveProjectV2Item": {"item": {"id": "second", "isArchived": True}}}]
                )
                with patch("builtins.print") as output:
                    self.assertEqual(tracker.get_project_items(), {"azure/aks": "first"})
                output.assert_called_once_with(
                    "Archived duplicate azure/aks: second; keeping first."
                )
                self.assertEqual(graphql.call_args.args, (
                    tracker.ARCHIVE_PROJECT_ITEM_MUTATION,
                    {"projectId": "project", "itemId": "second"},
                ))

    @patch.object(tracker, "run_graphql")
    def test_archive_failure_stops_cleanup(self, graphql):
        graphql.side_effect = [
            project_page([
                {"id": "first", "content": {"title": "Azure/AKS"}},
                {"id": "second", "content": {"title": "azure/aks"}},
            ]),
            RuntimeError("archive denied"),
        ]
        with self.assertRaisesRegex(RuntimeError, "archive denied"):
            tracker.get_project_items()

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
            if query == tracker.ARCHIVE_PROJECT_ITEM_MUTATION:
                return {"archiveProjectV2Item": {"item": {
                    "id": variables["itemId"], "isArchived": True,
                }}}
            return {}

        graphql.side_effect = respond
        with patch.object(tracker, "TARGET_REPOSITORIES", targets):
            tracker.main()

        calls = graphql.call_args_list
        creates = [c.args[1] for c in calls if c.args[0] == tracker.ADD_PROJECT_ITEM_MUTATION]
        self.assertEqual([c["title"] for c in creates], ["org/b", "org/c"])
        self.assertFalse(any(c.args[0] == tracker.UPDATE_FIELD_MUTATION for c in calls))
        moves = [c.args[1] for c in calls if c.args[0] == tracker.UPDATE_POSITION_MUTATION]
        self.assertEqual([(c["itemId"], c["afterId"]) for c in moves], [
            ("org/b", None), ("org/c", "org/b"), ("existing", "org/c"),
        ])
        self.rankings.assert_called_once_with([
            (50.0, "org/b", "org/b"), (10.0, "org/c", "org/c"),
            (5.0, "org/a", "existing"),
        ])

    @patch.object(tracker, "run_graphql")
    def test_repository_failure_fails_run(self, graphql):
        graphql.side_effect = [project_page([]), RuntimeError("API error")]
        with patch.object(tracker, "TARGET_REPOSITORIES", [{"owner": "org", "name": "a"}]):
            with self.assertRaisesRegex(RuntimeError, "org/a"):
                tracker.main()
        self.rankings.assert_not_called()

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


class RankingTests(unittest.TestCase):
    def field(self, name="Ranking"):
        options = tracker.RANKING_OPTIONS if name == "Ranking" else tracker.TREND_OPTIONS
        if name == "WoW Delta":
            return {"__typename": "ProjectV2Field", "id": name, "name": name, "dataType": "NUMBER"}
        return {
            "__typename": "ProjectV2MultiSelectField" if name == "Ranking" else "ProjectV2SingleSelectField",
            "id": name, "name": name,
            "multiSelectOptions" if name == "Ranking" else "options": [
                {"id": option["name"], **option}
                for option in options
            ],
        }

    def fields(self, *nodes):
        return {"node": {"fields": {
            "nodes": list(nodes), "pageInfo": {"hasNextPage": False},
        }}}

    @patch.object(tracker, "run_graphql")
    def test_existing_field_is_reused(self, graphql):
        graphql.return_value = self.fields(*(self.field(n) for n in ("Ranking", "WoW Delta", "WoW Trend")))
        fields, legacy = tracker.get_dashboard_fields()
        field_id, options = fields["Ranking"]
        self.assertEqual(field_id, "Ranking")
        self.assertEqual(options["Top 3"], "Top 3")
        self.assertIsNone(legacy)
        graphql.assert_called_once()

    @patch.object(tracker, "run_graphql")
    def test_missing_ranking_options_are_added_without_replacing_existing_options(self, graphql):
        for count in (0, 2, 3):
            with self.subTest(existing_required_options=count):
                ranking = self.field()
                ranking["multiSelectOptions"] = ranking["multiSelectOptions"][:count] + [{
                    "id": "custom", "name": "Custom", "color": "BLUE", "description": "Keep me",
                }]
                existing = ranking["multiSelectOptions"]
                updated = self.field()
                for option in updated["multiSelectOptions"][count:]:
                    option["id"] = "new-" + option["name"]
                updated["multiSelectOptions"].append(existing[-1])
                graphql.reset_mock()
                graphql.side_effect = [
                    self.fields(ranking, self.field("WoW Delta"), self.field("WoW Trend")),
                    {"updateProjectV2Field": {"projectV2Field": ranking}},
                    self.fields(updated, self.field("WoW Delta"), self.field("WoW Trend")),
                    self.fields(updated, self.field("WoW Delta"), self.field("WoW Trend")),
                ]
                fields, legacy = tracker.get_dashboard_fields()
                self.assertIsNone(legacy)
                self.assertEqual(fields["Ranking"], (
                    "Ranking", {o["name"]: o["id"] for o in updated["multiSelectOptions"]},
                ))
                update = graphql.call_args_list[1]
                self.assertEqual(update.args, (tracker.UPDATE_RANKING_OPTIONS_MUTATION, {
                    "fieldId": "Ranking", "options": existing + tracker.RANKING_OPTIONS[count:],
                }))
                self.assertIn("id name color description", tracker.RANKING_FIELD_QUERY)
                self.assertEqual(tracker.get_dashboard_fields()[0], fields)
                self.assertEqual(graphql.call_args_list[2].args, (
                    tracker.RANKING_FIELD_QUERY, {"projectId": tracker.PROJECT_ID},
                ))
                self.assertEqual(graphql.call_count, 4)

    @patch.object(tracker, "run_graphql")
    def test_ranking_option_refresh_still_validates_names_and_colors(self, graphql):
        for mismatch in ("missing", "wrong color", "different name"):
            with self.subTest(mismatch=mismatch):
                ranking = self.field()
                ranking["multiSelectOptions"] = ranking["multiSelectOptions"][:2]
                refreshed = self.field()
                if mismatch == "missing":
                    refreshed["multiSelectOptions"] = refreshed["multiSelectOptions"][:2]
                elif mismatch == "wrong color":
                    refreshed["multiSelectOptions"][2]["color"] = "GRAY"
                else:
                    refreshed["multiSelectOptions"][2]["name"] = "Previous Week’s Top 3"
                graphql.reset_mock()
                graphql.side_effect = [
                    self.fields(ranking),
                    {"updateProjectV2Field": {"projectV2Field": self.field()}},
                    self.fields(refreshed),
                ]
                with patch.object(tracker, "configure_dashboard_view") as view:
                    with self.assertRaisesRegex(ValueError, "Received options:") as error:
                        tracker.update_rankings([(100, "org/a", "a")])
                self.assertIn(repr([
                    (o["name"], o["color"]) for o in refreshed["multiSelectOptions"]
                ]), str(error.exception))
                view.assert_not_called()
                self.assertEqual(graphql.call_count, 3)

    @patch.object(tracker, "run_graphql")
    def test_ranking_option_refresh_failure_stops_sync(self, graphql):
        ranking = self.field()
        ranking["multiSelectOptions"] = ranking["multiSelectOptions"][:2]
        graphql.side_effect = [
            self.fields(ranking), {}, RuntimeError("read denied"),
        ]
        with patch.object(tracker, "configure_dashboard_view") as view:
            with self.assertRaisesRegex(RuntimeError, "read denied"):
                tracker.update_rankings([(100, "org/a", "a")])
        view.assert_not_called()

    @patch.object(tracker, "run_graphql")
    def test_ranking_option_upgrade_failure_stops_sync(self, graphql):
        ranking = self.field()
        ranking["multiSelectOptions"] = ranking["multiSelectOptions"][:2]
        graphql.side_effect = [
            self.fields(ranking), RuntimeError("update denied"),
        ]
        with patch.object(tracker, "configure_dashboard_view") as view:
            with self.assertRaisesRegex(RuntimeError, "update denied"):
                tracker.update_rankings([(100, "org/a", "a")])
        view.assert_not_called()
        self.assertEqual(graphql.call_count, 2)

    @patch.object(tracker, "run_graphql")
    def test_missing_field_is_created_with_colors(self, graphql):
        graphql.side_effect = [
            self.fields(),
            *({"createProjectV2Field": {"projectV2Field": self.field(n)}}
              for n in ("Ranking", "WoW Delta", "WoW Trend")),
        ]
        tracker.get_dashboard_fields()
        create = graphql.call_args_list[1]
        self.assertEqual(create.args[0], tracker.CREATE_DASHBOARD_FIELD_MUTATION)
        self.assertEqual(create.args[1]["multi"], tracker.RANKING_OPTIONS)

    @patch.object(tracker, "run_graphql")
    def test_invalid_field_is_rejected(self, graphql):
        graphql.return_value = self.fields({"__typename": "ProjectV2Field", "name": "Ranking", "dataType": "TEXT"})
        with self.assertRaisesRegex(ValueError, "MULTI_SELECT"):
            tracker.get_dashboard_fields()
        for count in (2, 4):
            with self.subTest(existing_required_options=count):
                field = self.field()
                field["multiSelectOptions"] = field["multiSelectOptions"][:count]
                field["multiSelectOptions"][0]["color"] = "RED"
                graphql.reset_mock()
                graphql.return_value = self.fields(field)
                with self.assertRaisesRegex(ValueError, "GREEN"):
                    tracker.get_dashboard_fields()
                graphql.assert_called_once()

    @patch.object(tracker, "run_graphql")
    @patch.object(tracker, "configure_dashboard_view")
    @patch.object(tracker, "get_dashboard_fields")
    def test_top_three_priority_and_strict_threshold(self, field, view, graphql):
        field.return_value = ({
            "Ranking": ("ranking", {o["name"]: o["name"] for o in tracker.RANKING_OPTIONS}),
            "WoW Delta": ("delta", {}),
            "WoW Trend": ("trend", {o["name"]: o["name"] for o in tracker.TREND_OPTIONS}),
        }, None)
        graphql.return_value = {"node": {"content": {"id": "draft", "body": "Repository"}}}
        rows = [(score, f"org/{i}", str(i)) for i, score in enumerate(
            [30000, 20000, 15000, 10000.01, 10000, 9999]
        )]
        tracker.update_rankings(rows)
        ranking_calls = [
            call for call in graphql.call_args_list
            if call.args[1].get("fieldId") == "ranking"
        ]
        self.assertEqual([c.args[1].get("options") for c in ranking_calls],
                         [["Top 3"], ["Top 3"], ["Top 3"], ["Over 10,000"], None, None])
        for call in ranking_calls[-2:]:
            self.assertEqual(call.args[0], tracker.CLEAR_RANKING_MUTATION)
        self.assertNotIn("Standard", [o["name"] for o in tracker.RANKING_OPTIONS])

    @patch.object(tracker, "run_graphql")
    def test_single_select_migration_keeps_legacy_until_publication(self, graphql):
        legacy = {"__typename": "ProjectV2SingleSelectField", "id": "old", "name": "Ranking"}
        graphql.side_effect = [
            self.fields(legacy), {},
            *({"createProjectV2Field": {"projectV2Field": self.field(n)}}
              for n in ("Ranking", "WoW Delta", "WoW Trend")),
        ]
        fields, old_id = tracker.get_dashboard_fields()
        self.assertEqual(old_id, "old")
        self.assertEqual(fields["Ranking"][0], "Ranking")
        self.assertEqual(graphql.call_args_list[1].args[0], tracker.RENAME_FIELD_MUTATION)

    @patch.object(tracker, "run_graphql")
    @patch.object(tracker, "configure_dashboard_view")
    @patch.object(tracker, "get_dashboard_fields")
    def test_delta_trend_signs_and_previous_labels_are_published(self, fields, view, graphql):
        fields.return_value = ({
            "Ranking": ("ranking", {o["name"]: o["name"] for o in tracker.RANKING_OPTIONS}),
            "WoW Delta": ("delta", {}),
            "WoW Trend": ("trend", {o["name"]: o["name"] for o in tracker.TREND_OPTIONS}),
        }, None)
        today = datetime.now(timezone(timedelta(hours=-8))).date()
        previous = today - timedelta(days=today.weekday() + 7)
        history = {previous.isoformat(): {"score": 100, "labels": ["Over 10,000"]}}
        body = "<!-- popularity-weekly:" + json.dumps(history) + " -->"
        graphql.return_value = {"node": {"content": {"id": "draft", "body": body}}}
        tracker.update_rankings([(110, "org/a", "a"), (100, "org/b", "b"), (90, "org/c", "c")])
        delta_calls = [c for c in graphql.call_args_list if c.args[1].get("fieldId") == "delta"]
        self.assertEqual([c.args[1]["value"] for c in delta_calls], [10, 0, -10])
        trend_calls = [c for c in graphql.call_args_list if c.args[1].get("fieldId") == "trend"]
        self.assertEqual([c.args[1].get("optionId") for c in trend_calls], ["Increase", None, "Decrease"])
        self.assertEqual(trend_calls[1].args[0], tracker.CLEAR_RANKING_MUTATION)
        ranking_calls = [c for c in graphql.call_args_list if c.args[1].get("fieldId") == "ranking"]
        self.assertTrue(all(c.args[1]["options"] == ["Top 3", "Previous Week's Over 10,000"] for c in ranking_calls))
        saved = [c for c in graphql.call_args_list if c.args[0] == tracker.UPDATE_SNAPSHOT_MUTATION]
        self.assertEqual(len(saved), 3)

    @patch.object(tracker, "run_graphql")
    @patch.object(tracker, "configure_dashboard_view")
    @patch.object(tracker, "get_dashboard_fields")
    def test_write_failure_does_not_commit_snapshot_or_remove_legacy(self, fields, view, graphql):
        fields.return_value = ({
            "Ranking": ("ranking", {}),
            "WoW Delta": ("delta", {}),
            "WoW Trend": ("trend", {}),
        }, "legacy")
        graphql.side_effect = [
            {"node": {"content": {"id": "draft", "body": ""}}},
            RuntimeError("write denied"),
        ]
        with self.assertRaisesRegex(RuntimeError, "write denied"):
            tracker.update_rankings([(100, "org/a", "a")])
        view.assert_not_called()
        self.assertFalse(any(c.args[0] == tracker.UPDATE_SNAPSHOT_MUTATION for c in graphql.call_args_list))


class WeeklySnapshotTests(unittest.TestCase):
    def snapshot(self, history):
        return "Repository notes\n\n<!-- popularity-weekly:" + json.dumps(history) + " -->"

    def test_first_run_leaves_delta_blank(self):
        delta, labels, body = tracker.weekly_snapshot(
            "Repository notes", 200, ["Top 3"], date(2026, 10, 19)
        )
        self.assertIsNone(delta)
        self.assertEqual(labels, ["Top 3"])
        self.assertTrue(body.startswith("Repository notes"))

    def test_week_rollover_and_same_week_rerun(self):
        _, _, body = tracker.weekly_snapshot("", 100, ["Top 3"], date(2026, 10, 12))
        delta, labels, body = tracker.weekly_snapshot(body, 120, ["Over 10,000"], date(2026, 10, 19))
        self.assertEqual(delta, 20)
        self.assertEqual(labels, ["Over 10,000", "Previous Week's Top 3"])
        delta, labels, body = tracker.weekly_snapshot(body, 130, ["Top 3"], date(2026, 10, 23))
        self.assertEqual(delta, 30)
        self.assertEqual(labels, ["Top 3", "Previous Week's Top 3"])
        self.assertEqual(body.count("<!-- popularity-weekly:"), 1)

    def test_negative_zero_and_missing_week(self):
        body = self.snapshot({"2026-10-12": {"score": 100, "labels": ["Over 10,000"]}})
        for score, expected in [(90, -10), (100, 0)]:
            delta, labels, _ = tracker.weekly_snapshot(body, score, [], date(2026, 10, 19))
            self.assertEqual(delta, expected)
            self.assertEqual(labels, ["Previous Week's Over 10,000"])
        delta, labels, _ = tracker.weekly_snapshot(body, 200, [], date(2026, 10, 26))
        self.assertIsNone(delta)
        self.assertEqual(labels, [])

    def test_year_boundary(self):
        body = self.snapshot({"2026-12-28": {"score": 100, "labels": []}})
        delta, _, _ = tracker.weekly_snapshot(body, 101.23, [], date(2027, 1, 4))
        self.assertEqual(delta, 1.23)

    def test_corrupt_snapshot_fails_explicitly(self):
        for body in [
            "<!-- popularity-weekly:bad -->",
            "<!-- popularity-weekly:unfinished",
            self.snapshot({"2026-10-13": {"score": 100, "labels": []}}),
            self.snapshot({"2026-10-12": {"score": "100", "labels": []}}),
        ]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                tracker.weekly_snapshot(body, 10, [], date(2026, 10, 19))


if __name__ == "__main__":
    unittest.main()
