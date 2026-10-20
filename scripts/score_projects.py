import os
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
import requests

# Environment Variables
GITHUB_TOKEN = os.getenv("GH_TOKEN")
PROJECT_ID = os.getenv("GH_PROJECT_ID")  # The Node ID of your GitHub Project v2
SCORE_FIELD_ID = os.getenv("GH_SCORE_FIELD_ID")  # Custom field ID for the Score

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}

# Target Kubernetes ecosystem repositories to evaluate
TARGET_REPOSITORIES = [
    {"owner": "kubernetes", "name": "kubernetes"},
    {"owner": "kubernetes", "name": "enhancements"},
    {"owner": "kubernetes", "name": "community"},
    {"owner": "kubernetes", "name": "minikube"},
    {"owner": "helm", "name": "helm"},
    {"owner": "prometheus", "name": "prometheus"},
    {"owner": "containerd", "name": "containerd"},
    {"owner": "containerd", "name": "nerdctl"},
    {"owner": "moby", "name": "moby"},
    {"owner": "moby", "name": "buildkit"},
    {"owner": "envoyproxy", "name": "envoy"},
    {"owner": "argoproj", "name": "argo-cd"},
    {"owner": "docker", "name": "for-win"},
    {"owner": "microsoft", "name": "Windows-Containers"},
    {"owner": "microsoft", "name": "windows-container-tools"},
    {"owner": "microsoft", "name": "containers-toolkit"},
    {"owner": "Azure", "name": "AKS"},
]

# GraphQL query to fetch quantitative and qualitative repository signals
REPO_METRICS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    stargazerCount
    forkCount
    issues(states: OPEN) {
      totalCount
    }
    mergedPRs: pullRequests(states: MERGED) {
      totalCount
    }
    closedPRs: pullRequests(states: CLOSED) {
      totalCount
    }
    defaultBranchRef {
      target {
        ... on Commit {
          history(since: "2026-07-01T00:00:00Z") {
            totalCount
          }
        }
      }
    }
  }
}
"""

PROJECT_QUERY = """
query($projectId: ID!, $cursor: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      field(name: "Popularity Score") {
        ... on ProjectV2Field { id dataType }
      }
      items(first: 100, after: $cursor) {
        nodes {
          id
          isArchived
          content { ... on DraftIssue { title } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

ADD_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $title: String!, $body: String!) {
  addProjectV2DraftIssue(input: {
    projectId: $projectId, title: $title, body: $body
  }) {
    projectItem { id }
  }
}
"""

UPDATE_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: Float!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { number: $value }
  }) {
    projectV2Item {
      id
    }
  }
}
"""


UPDATE_POSITION_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $afterId: ID) {
  updateProjectV2ItemPosition(input: {
    projectId: $projectId, itemId: $itemId, afterId: $afterId
  }) {
    clientMutationId
  }
}
"""

RANKING_OPTIONS = [
    {"name": "Top 3", "color": "GREEN", "description": "Top three by popularity score"},
    {"name": "Over 10,000", "color": "YELLOW", "description": "Score above 10,000, outside the top three"},
    {"name": "Previous Week's Top 3", "color": "GREEN", "description": "Top three in the previous calendar week"},
    {"name": "Previous Week's Over 10,000", "color": "YELLOW", "description": "Over 10,000 in the previous calendar week"},
]

TREND_OPTIONS = [
    {"name": "Increase", "color": "GREEN", "description": "Score increased from last week"},
    {"name": "Decrease", "color": "RED", "description": "Score decreased from last week"},
]

RANKING_FIELD_QUERY = """
query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2Field { id name dataType }
          ... on ProjectV2SingleSelectField {
            id name options { id name color }
          }
          ... on ProjectV2IterationField { name }
          ... on ProjectV2MultiSelectField {
            id name multiSelectOptions { id name color description }
          }
        }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""

UPDATE_RANKING_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
    value: { singleSelectOptionId: $optionId }
  }) {
    projectV2Item { id }
  }
}
"""


CLEAR_RANKING_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!) {
  clearProjectV2ItemFieldValue(input: {
    projectId: $projectId, itemId: $itemId, fieldId: $fieldId
  }) {
    projectV2Item { id }
  }
}
"""


def get_dashboard_fields():
  fields = run_graphql(RANKING_FIELD_QUERY, {"projectId": PROJECT_ID})["node"]["fields"]
  if fields["pageInfo"]["hasNextPage"]:
    raise ValueError("Ranking field lookup supports at most 100 project fields.")
  by_name = {field["name"]: field for field in fields["nodes"]}
  legacy = by_name.get("Ranking (legacy)")
  ranking = by_name.get("Ranking")
  if ranking and ranking["__typename"] == "ProjectV2SingleSelectField":
    if legacy:
      raise ValueError("Both single-select Ranking and Ranking (legacy) exist; resolve before migration.")
    run_graphql(RENAME_FIELD_MUTATION, {"fieldId": ranking["id"], "name": "Ranking (legacy)"})
    legacy = by_name.pop("Ranking")
  result = {}
  for name, kind, options in [
      ("Ranking", "MULTI_SELECT", RANKING_OPTIONS),
      ("WoW Delta", "NUMBER", []),
      ("WoW Trend", "SINGLE_SELECT", TREND_OPTIONS),
  ]:
    field = by_name.get(name)
    if not field:
      field = run_graphql(CREATE_DASHBOARD_FIELD_MUTATION, {
          "projectId": PROJECT_ID, "name": name, "kind": kind,
          "single": options if kind == "SINGLE_SELECT" else None,
          "multi": options if kind == "MULTI_SELECT" else None,
      })["createProjectV2Field"]["projectV2Field"]
    actual = {
        "ProjectV2MultiSelectField": "MULTI_SELECT",
        "ProjectV2SingleSelectField": "SINGLE_SELECT",
    }.get(field["__typename"], field.get("dataType"))
    if actual != kind:
      raise ValueError(f"{name} must have type {kind}.")
    values = field.get("multiSelectOptions", field.get("options", []))
    option_map = {option["name"]: option for option in values}
    missing = [option for option in options if option["name"] not in option_map]
    if kind == "MULTI_SELECT" and missing:
      for option in options:
        if option["name"] in option_map and option_map[option["name"]]["color"] != option["color"]:
          raise ValueError(f"{name} needs a {option['color']} option named {option['name']}.")
      run_graphql(UPDATE_RANKING_OPTIONS_MUTATION, {
          "fieldId": field["id"], "options": values + missing,
      })
      refreshed = run_graphql(RANKING_FIELD_QUERY, {"projectId": PROJECT_ID})["node"]["fields"]
      field = next(node for node in refreshed["nodes"] if node.get("id") == field["id"])
      option_map = {option["name"]: option for option in field["multiSelectOptions"]}
    for option in options:
      if option["name"] not in option_map or option_map[option["name"]]["color"] != option["color"]:
        received = [(value["name"], value["color"]) for value in option_map.values()]
        raise ValueError(
            f"{name} needs a {option['color']} option named {option['name']}. "
            f"Received options: {received!r}"
        )
    result[name] = (field["id"], {name: option["id"] for name, option in option_map.items()})
  return result, legacy["id"] if legacy else None


CREATE_DASHBOARD_FIELD_MUTATION = """
mutation($projectId: ID!, $name: String!, $kind: ProjectV2CustomFieldType!,
         $single: [ProjectV2SingleSelectFieldOptionInput!],
         $multi: [ProjectV2MultiSelectFieldOptionInput!]) {
  createProjectV2Field(input: {
    projectId: $projectId, name: $name, dataType: $kind,
    singleSelectOptions: $single, multiSelectOptions: $multi
  }) {
    projectV2Field {
      __typename
      ... on ProjectV2Field { id name dataType }
      ... on ProjectV2SingleSelectField { id name options { id name color } }
      ... on ProjectV2MultiSelectField { id name multiSelectOptions { id name color } }
    }
  }
}
"""

RENAME_FIELD_MUTATION = """
mutation($fieldId: ID!, $name: String!) {
  updateProjectV2Field(input: {fieldId: $fieldId, name: $name}) {
    clientMutationId
  }
}
"""

UPDATE_RANKING_OPTIONS_MUTATION = """
mutation($fieldId: ID!, $options: [ProjectV2MultiSelectFieldOptionInput!]!) {
  updateProjectV2Field(input: {fieldId: $fieldId, multiSelectOptions: $options}) {
    projectV2Field {
      ... on ProjectV2MultiSelectField {
        id multiSelectOptions { id name color description }
      }
    }
  }
}
"""

UPDATE_MULTI_RANKING_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $options: [String!]!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
    value: { multiSelectOptionIds: $options }
  }) { projectV2Item { id } }
}
"""

SNAPSHOT_QUERY = """
query($itemId: ID!) {
  node(id: $itemId) {
    ... on ProjectV2Item {
      content { ... on DraftIssue { id body } }
    }
  }
}
"""

UPDATE_SNAPSHOT_MUTATION = """
mutation($draftId: ID!, $body: String!) {
  updateProjectV2DraftIssue(input: {draftIssueId: $draftId, body: $body}) {
    clientMutationId
  }
}
"""

SNAPSHOT_PATTERN = re.compile(r"<!-- popularity-weekly:(.*?) -->", re.DOTALL)


def weekly_snapshot(body, score, labels, today):
  monday = today - timedelta(days=today.weekday())
  week = monday.isoformat()
  previous_week = (monday - timedelta(days=7)).isoformat()
  matches = list(SNAPSHOT_PATTERN.finditer(body))
  if body.count("<!-- popularity-weekly:") != len(matches) or len(matches) > 1:
    raise ValueError("Invalid weekly snapshot marker in draft body.")
  history = json.loads(matches[0].group(1)) if matches else {}
  if not isinstance(history, dict):
    raise ValueError("Weekly snapshot must be an object.")
  for key, value in history.items():
    parsed = date.fromisoformat(key)
    if parsed.weekday() != 0 or key > week:
      raise ValueError(f"Invalid or future snapshot week: {key}")
    if (not isinstance(value, dict) or set(value) != {"score", "labels"}
        or isinstance(value["score"], bool) or not isinstance(value["score"], (int, float))
        or not math.isfinite(value["score"]) or not isinstance(value["labels"], list)
        or any(label not in ("Top 3", "Over 10,000") for label in value["labels"])):
      raise ValueError(f"Invalid weekly snapshot for {key}.")
  previous = history.get(previous_week)
  delta = round(score - previous["score"], 2) if previous else None
  combined = labels + [
      f"Previous Week's {label}" for label in previous["labels"]
  ] if previous else labels[:]
  history = {previous_week: previous} if previous else {}
  history[week] = {"score": score, "labels": labels}
  marker = "<!-- popularity-weekly:" + json.dumps(history, sort_keys=True) + " -->"
  updated_body = SNAPSHOT_PATTERN.sub(lambda match: marker, body) if matches else body.rstrip() + "\n\n" + marker
  return delta, combined, updated_body


def update_rankings(scored_items):
  fields, legacy_id = get_dashboard_fields()
  today = datetime.now(timezone(timedelta(hours=-8))).date()
  updates = []
  for rank, (score, title, item_id) in enumerate(scored_items, start=1):
    labels = ["Top 3"] if rank <= 3 else ["Over 10,000"] if score > 10000 else []
    draft = run_graphql(SNAPSHOT_QUERY, {"itemId": item_id})["node"]["content"]
    delta, labels, body = weekly_snapshot(draft["body"] or "", score, labels, today)
    updates.append((score, title, item_id, draft["id"], body, delta, labels))
  for score, title, item_id, draft_id, body, delta, labels in updates:
    for name, value in [
        ("Popularity Score", score), ("WoW Delta", delta),
        ("WoW Trend", "Increase" if delta is not None and delta > 0 else
         "Decrease" if delta is not None and delta < 0 else None),
        ("Ranking", labels),
    ]:
      field_id, options = (SCORE_FIELD_ID, {}) if name == "Popularity Score" else fields[name]
      variables = {"projectId": PROJECT_ID, "itemId": item_id, "fieldId": field_id}
      if value is None or value == []:
        mutation = CLEAR_RANKING_MUTATION
      elif name == "Ranking":
        mutation = UPDATE_MULTI_RANKING_MUTATION
        variables["options"] = [options[label] for label in value]
      elif name == "WoW Trend":
        mutation = UPDATE_RANKING_MUTATION
        variables["optionId"] = options[value]
      else:
        mutation = UPDATE_FIELD_MUTATION
        variables["value"] = value
      run_graphql(mutation, variables)
    run_graphql(UPDATE_SNAPSHOT_MUTATION, {"draftId": draft_id, "body": body})
    print(f"-> {title}: delta={delta}, ranking={labels}")
  configure_dashboard_view(fields, legacy_id)


def configure_dashboard_view(fields, legacy_id):
  query = """
  query($id: ID!, $cursor: String) {
    node(id: $id) { ... on ProjectV2 {
      views(first: 100, after: $cursor) {
        nodes { id configuration { visibleFields(first: 100) {
          nodes {
            ... on ProjectV2Field { id }
            ... on ProjectV2SingleSelectField { id }
            ... on ProjectV2MultiSelectField { id }
            ... on ProjectV2IterationField { id }
          }
          pageInfo { hasNextPage }
        } } }
        pageInfo { hasNextPage endCursor }
      }
    } }
  }
  """
  cursor = None
  while True:
    views = run_graphql(query, {"id": PROJECT_ID, "cursor": cursor})["node"]["views"]
    for view in views["nodes"]:
      visible = view["configuration"]["visibleFields"]
      if visible["pageInfo"]["hasNextPage"]:
        raise ValueError("View has more than 100 visible fields.")
      ids = [field["id"] for field in visible["nodes"] if field["id"] != legacy_id]
      for field_id in [SCORE_FIELD_ID] + [field[0] for field in fields.values()]:
        if field_id not in ids:
          ids.append(field_id)
      run_graphql("""
      mutation($id: ID!, $fields: [ID!]!) {
        updateProjectV2View(input: {viewId: $id, configuration: {visibleFieldIds: $fields}}) {
          clientMutationId
        }
      }
      """, {"id": view["id"], "fields": ids})
    if not views["pageInfo"]["hasNextPage"]:
      break
    cursor = views["pageInfo"]["endCursor"]
  if legacy_id:
    run_graphql("""
    mutation($id: ID!) {
      deleteProjectV2Field(input: {fieldId: $id}) { clientMutationId }
    }
    """, {"id": legacy_id})


def run_graphql(query, variables=None):
  response = requests.post(
      "https://api.github.com/graphql",
      json={"query": query, "variables": variables},
      headers=HEADERS,
      timeout=60,
  )
  if response.status_code != 200:
    raise RuntimeError(f"GraphQL API Error: {response.status_code} - {response.text}")
  
  result = response.json()
  if "errors" in result:
    raise RuntimeError(f"GraphQL Errors: {result['errors']}")
  return result["data"]


ARCHIVE_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $itemId: ID!) {
  archiveProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
    item { id isArchived }
  }
}
"""


def get_project_items():
  items = {}
  duplicates = []
  targets = {
      f"{repo['owner']}/{repo['name']}".casefold()
      for repo in TARGET_REPOSITORIES
  }
  cursor = None
  while True:
    project = run_graphql(PROJECT_QUERY, {
        "projectId": PROJECT_ID, "cursor": cursor
    })["node"]
    if not project or "items" not in project:
      raise ValueError("GH_PROJECT_ID must identify an accessible ProjectV2.")
    field = project["field"]
    if not field or field.get("id") != SCORE_FIELD_ID or field.get("dataType") != "NUMBER":
      raise ValueError(
          "GH_SCORE_FIELD_ID must identify the numeric Popularity Score field."
      )
    for item in project["items"]["nodes"]:
      if item.get("isArchived"):
        continue
      content = item.get("content")
      if content and "title" in content:
        title = content["title"].casefold()
        if title in items:
          if title in targets and item["id"] != items[title]:
            duplicates.append((title, item["id"]))
          continue
        items[title] = item["id"]
    page = project["items"]["pageInfo"]
    if not page["hasNextPage"]:
      break
    cursor = page["endCursor"]
  # Finish pagination before archiving so mutations cannot shift the page cursors.
  for title, item_id in duplicates:
    result = run_graphql(ARCHIVE_PROJECT_ITEM_MUTATION, {
        "projectId": PROJECT_ID, "itemId": item_id
    })["archiveProjectV2Item"]["item"]
    if result["id"] != item_id or not result["isArchived"]:
      raise RuntimeError(f"Could not archive duplicate project item {item_id}.")
    print(f"Archived duplicate {title}: {item_id}; keeping {items[title]}.")
  return items


def calculate_score(repo_data):
  """Computes a composite score combining quantitative and qualitative signals.
  
  - Quantitative: Stargazer volume, fork count, and recent commit velocity.
  - Qualitative: PR merge ratio (merged vs closed) indicating maintenance health.
  """
  stars = repo_data["stargazerCount"]
  forks = repo_data["forkCount"]
  
  # Handle safe extraction of commit history
  branch_ref = repo_data.get("defaultBranchRef")
  recent_commits = 0
  if branch_ref and branch_ref.get("target"):
    recent_commits = branch_ref["target"]["history"]["totalCount"]

  # Qualitative proxy: PR Merge Ratio
  merged_prs = repo_data["mergedPRs"]["totalCount"]
  closed_prs = repo_data["closedPRs"]["totalCount"]
  total_handled_prs = merged_prs + closed_prs
  
  pr_merge_ratio = (merged_prs / total_handled_prs) if total_handled_prs > 0 else 0.5

  # Composite Scoring Formula
  base_score = (stars * 0.05) + (forks * 1.5) + (recent_commits * 2.0)
  qualitative_multiplier = 0.5 + (pr_merge_ratio * 0.5)
  
  final_score = base_score * qualitative_multiplier
  return round(final_score, 2)


def main():
  if not GITHUB_TOKEN:
    raise ValueError("GH_TOKEN environment variable is missing.")
  if bool(PROJECT_ID) != bool(SCORE_FIELD_ID):
    raise ValueError("Set both GH_PROJECT_ID and GH_SCORE_FIELD_ID for project sync.")
  print("Starting Kubernetes OSS popularity evaluation using GitHub App Token...")
  items = get_project_items() if PROJECT_ID else {}
  scored_items = []
  failures = []

  for repo_info in TARGET_REPOSITORIES:
    owner = repo_info["owner"]
    name = repo_info["name"]
    print(f"\nAnalyzing {owner}/{name}...")

    try:
      data = run_graphql(REPO_METRICS_QUERY, {"owner": owner, "name": name})
      repo = data["repository"]
      
      score = calculate_score(repo)
      print(f"-> Calculated Popularity Score: {score}")

      # Sync to Project v2 if Project ID and Field ID are configured
      if PROJECT_ID and SCORE_FIELD_ID:
        title = repo["nameWithOwner"]
        key = title.casefold()
        item_id = items.get(key)
        if not item_id:
          # Recheck before creating; the startup snapshot may be stale.
          items.update(get_project_items())
          item_id = items.get(key)
        if not item_id:
          add_res = run_graphql(ADD_PROJECT_ITEM_MUTATION, {
              "projectId": PROJECT_ID,
              "title": title,
              "body": f"Repository: https://github.com/{title}"
          })
          item_id = add_res["addProjectV2DraftIssue"]["projectItem"]["id"]
          items[key] = item_id

        scored_items.append((score, title, item_id))
        print(f"-> Prepared {owner}/{name} for project sync.")
      else:
        print("-> Skipped project board sync (GH_PROJECT_ID or GH_SCORE_FIELD_ID not provided).")

    except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as e:
      failures.append(f"{owner}/{name}")
      print(f"Error processing {owner}/{name}: {e}")

  if failures:
    raise RuntimeError(f"Evaluation failed for: {', '.join(failures)}")
  scored_items.sort(key=lambda item: (-item[0], item[1]))
  if scored_items:
    update_rankings(scored_items)
  after_id = None
  for score, title, item_id in scored_items:
    run_graphql(UPDATE_POSITION_MUTATION, {
        "projectId": PROJECT_ID, "itemId": item_id, "afterId": after_id
    })
    after_id = item_id
  print("\nEvaluation and sync complete!")


if __name__ == "__main__":
  main()