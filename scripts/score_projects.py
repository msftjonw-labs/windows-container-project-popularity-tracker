import os
import sys
import requests

# Environment Variables
GITHUB_TOKEN = os.getenv("GH_TOKEN")
PROJECT_ID = os.getenv("GH_PROJECT_ID")  # The Node ID of your GitHub Project v2
SCORE_FIELD_ID = os.getenv("GH_SCORE_FIELD_ID")  # Custom field ID for the Score

if not GITHUB_TOKEN:
    print("Error: GH_TOKEN environment variable is missing.")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}

# Target Kubernetes ecosystem repositories to evaluate
TARGET_REPOSITORIES = [
    {"owner": "kubernetes", "name": "kubernetes"},
    {"owner": "moby", "name": "moby"},
    {"owner": "containerd", "name": "containerd"},
    {"owner": "Azure", "name": "AKS"},
    {"owner": "microsoft", "name": "WSL"},
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
    mergedPRs: pullRequests(states: MERGED, privacy: PUBLIC) {
      totalCount
    }
    closedPRs: pullRequests(states: CLOSED, privacy: PUBLIC) {
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

ADD_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item {
      id
    }
  }
}
"""

UPDATE_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { text: $value }
  }) {
    projectV2Item {
      id
    }
  }
}
"""


def run_graphql(query, variables={}):
  response = requests.post(
      "https://api.github.com/graphql",
      json={"query": query, "variables": variables},
      headers=HEADERS,
  )
  if response.status_code != 200:
    raise Exception(f"GraphQL API Error: {response.status_code} - {response.text}")
  
  result = response.json()
  if "errors" in result:
    raise Exception(f"GraphQL Errors: {result['errors']}")
  return result["data"]


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
  print("Starting Kubernetes OSS popularity evaluation using GitHub App Token...")
  
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
        # 1. Add repository item to the project board
        add_res = run_graphql(ADD_PROJECT_ITEM_MUTATION, {
            "projectId": PROJECT_ID,
            "contentId": repo["id"]
        })
        item_id = add_res["addProjectV2ItemById"]["item"]["id"]

        # 2. Update the custom score field on the project board item
        run_graphql(UPDATE_FIELD_MUTATION, {
            "projectId": PROJECT_ID,
            "itemId": item_id,
            "fieldId": SCORE_FIELD_ID,
            "value": str(score)
        })
        print(f"-> Successfully synced {owner}/{name} to GitHub Project v2.")
      else:
        print("-> Skipped project board sync (GH_PROJECT_ID or GH_SCORE_FIELD_ID not provided).")

    except Exception as e:
      print(f"Error processing {owner}/{name}: {e}")

  print("\nEvaluation and sync complete!")


if __name__ == "__main__":
  main()