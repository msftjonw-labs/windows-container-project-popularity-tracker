import os
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


def get_project_items():
  items = {}
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
      content = item.get("content")
      if content and "title" in content:
        title = content["title"]
        if title in items:
          raise ValueError(f"Duplicate project draft title: {title}")
        items[title] = item["id"]
    page = project["items"]["pageInfo"]
    if not page["hasNextPage"]:
      return items
    cursor = page["endCursor"]


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
        item_id = items.get(title)
        if not item_id:
          add_res = run_graphql(ADD_PROJECT_ITEM_MUTATION, {
              "projectId": PROJECT_ID,
              "title": title,
              "body": f"Repository: https://github.com/{title}"
          })
          item_id = add_res["addProjectV2DraftIssue"]["projectItem"]["id"]
          items[title] = item_id

        # 2. Update the custom score field on the project board item
        run_graphql(UPDATE_FIELD_MUTATION, {
            "projectId": PROJECT_ID,
            "itemId": item_id,
            "fieldId": SCORE_FIELD_ID,
            "value": score
        })
        scored_items.append((score, title, item_id))
        print(f"-> Successfully synced {owner}/{name} to GitHub Project v2.")
      else:
        print("-> Skipped project board sync (GH_PROJECT_ID or GH_SCORE_FIELD_ID not provided).")

    except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as e:
      failures.append(f"{owner}/{name}")
      print(f"Error processing {owner}/{name}: {e}")

  after_id = None
  for score, title, item_id in sorted(scored_items, key=lambda item: (-item[0], item[1])):
    run_graphql(UPDATE_POSITION_MUTATION, {
        "projectId": PROJECT_ID, "itemId": item_id, "afterId": after_id
    })
    after_id = item_id
  if failures:
    raise RuntimeError(f"Evaluation failed for: {', '.join(failures)}")
  print("\nEvaluation and sync complete!")


if __name__ == "__main__":
  main()