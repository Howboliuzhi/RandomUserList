import requests
import base64
import datetime
import random
import string

# ====== CẤU HÌNH ======
PAT = os.environ["GITHUB_TOKEN"]   # ✅ token tự động
OWNER = os.environ["GITHUB_REPOSITORY"].split("/")[0]
REPO = os.environ["GITHUB_REPOSITORY"].split("/")[1]
COAUTHOR_USERNAME = os.environ["INPUT_USERNAME"]
BASE_BRANCH = "main"                  # Nhánh gốc để tạo PR
MERGE_METHOD = "squash"               # "merge" | "squash" | "rebase"
DELETE_BRANCH_AFTER_MERGE = True      # Xóa nhánh sau khi merge
# =======================

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {PAT}",
}

def gh_get(url):
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def gh_post(url, json=None):
    r = requests.post(url, headers=HEADERS, json=json)
    r.raise_for_status()
    return r.json()

def gh_patch(url, json=None):
    r = requests.patch(url, headers=HEADERS, json=json)
    r.raise_for_status()
    return r.json()

def gh_put(url, json=None):
    r = requests.put(url, headers=HEADERS, json=json)
    # 405/409 có thể xảy ra khi không thể merge ngay
    if r.status_code >= 300:
        raise RuntimeError(f"PUT {url} failed: {r.status_code} {r.text}")
    return r.json()

def random_string(n=5):
    import secrets, string
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))

def main():
    # 1) Tác giả chính (ẩn email bằng noreply)
    me = gh_get(f"{API}/user")
    my_login = me["login"]
    my_id = me["id"]
    author_email = f"{my_id}+{my_login}@users.noreply.github.com"

    # 2) Thông tin Co-author
    co = gh_get(f"{API}/users/{COAUTHOR_USERNAME}")
    co_id = co["id"]
    co_login = co["login"]
    co_email = f"{co_id}+{co_login}@users.noreply.github.com"

    # 3) SHA nhánh gốc
    ref = gh_get(f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BASE_BRANCH}")
    base_sha = ref["object"]["sha"]

    # 4) Tạo nhánh mới
    new_branch = f"auto-pr-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    gh_post(f"{API}/repos/{OWNER}/{REPO}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": base_sha})

    # 5) Tạo file {username}.txt nội dung 5 ký tự random
    filename = f"{COAUTHOR_USERNAME}.txt"
    content = random_string(5)
    blob = gh_post(f"{API}/repos/{OWNER}/{REPO}/git/blobs", json={
        "content": base64.b64encode(content.encode()).decode(),
        "encoding": "base64"
    })
    blob_sha = blob["sha"]

    # 6) Lấy tree base & tạo tree mới
    base_commit = gh_get(f"{API}/repos/{OWNER}/{REPO}/git/commits/{base_sha}")
    tree_base_sha = base_commit["tree"]["sha"]
    new_tree = gh_post(f"{API}/repos/{OWNER}/{REPO}/git/trees", json={
        "base_tree": tree_base_sha,
        "tree": [{
            "path": filename,
            "mode": "100644",
            "type": "blob",
            "sha": blob_sha
        }]
    })
    tree_sha = new_tree["sha"]

    # 7) Tạo commit có Co-author trailer
    commit_msg = f"Add {filename}\n\nCo-authored-by: {co_login} <{co_email}>"
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_commit = gh_post(f"{API}/repos/{OWNER}/{REPO}/git/commits", json={
        "message": commit_msg,
        "tree": tree_sha,
        "parents": [base_sha],
        "author": {"name": my_login, "email": author_email, "date": now},
        "committer": {"name": my_login, "email": author_email, "date": now}
    })
    commit_sha = new_commit["sha"]

    # 8) Cập nhật ref nhánh mới
    gh_patch(f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{new_branch}",
             json={"sha": commit_sha})

    # 9) Tạo Pull Request
    pr = gh_post(f"{API}/repos/{OWNER}/{REPO}/pulls", json={
        "title": f"Add {filename}",
        "head": new_branch,
        "base": BASE_BRANCH,
        "body": f"This PR adds `{filename}` with random content.\n\nCo-authored-by: {co_login} <{co_email}>"
    })
    pr_number = pr["number"]
    pr_url = pr["html_url"]

    # 10) Merge ngay PR (nếu không bị chặn)
    # Với squash, nội dung commit hợp nhất mặc định lấy message của commit đơn lẻ (giữ Co-author trailer).
    merge_res = gh_put(f"{API}/repos/{OWNER}/{REPO}/pulls/{pr_number}/merge", json={
        "merge_method": MERGE_METHOD,
        # "commit_title": f"Merge PR #{pr_number}: Add {filename}",  # có thể đặt tiêu đề tùy ý
        # "commit_message": commit_msg,  # optional: ép message để giữ trailer
    })

    merged = merge_res.get("merged", False)
    sha_merged = merge_res.get("sha", "")
    print(f"✅ PR created: {pr_url}")
    if merged:
        print(f"✅ PR merged with method={MERGE_METHOD}. Merge SHA: {sha_merged}")
        # 11) Xóa nhánh sau khi merge (nếu bật)
        if DELETE_BRANCH_AFTER_MERGE:
            # DELETE /repos/{owner}/{repo}/git/refs/heads/{ref}
            del_url = f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{new_branch}"
            r = requests.delete(del_url, headers=HEADERS)
            if r.status_code in (204, 200):
                print(f"🧹 Deleted branch {new_branch}")
            else:
                print(f"⚠️ Could not delete branch {new_branch}: {r.status_code} {r.text}")
    else:
        print(f"⚠️ Could not merge PR automatically (likely branch protection/reviews/checks).")
        print("   You can review and merge manually at:", pr_url)

    print(f"📄 Created file: {filename} with content: '{content}'")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Error:", e)
        raise
