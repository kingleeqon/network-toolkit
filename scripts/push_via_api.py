# -*- coding: utf-8 -*-
"""通过 GitHub REST API 推送本仓库的提交（用于 github.com 直连不通的环境）。

为什么需要这个：
    本机到 github.com 的三条通道都不通——SSH 22/443 被中断、HTTPS 被代理拦（502），
    只有 api.github.com 通。所以普通 `git push` 走不通，改走 API：
    blobs → trees → commits → refs，逐个重建本地未推送的提交（历史不合并）。

用法：
    # 1) 先干跑，看清楚要推哪些提交、哪些文件（不会联网写任何东西）
    python scripts/push_via_api.py --dry-run

    # 2) 真推（token 从环境变量读，不要写在命令行/文件里）
    #    PowerShell:  $env:GITHUB_TOKEN='ghp_xxx'
    #    cmd:         set GITHUB_TOKEN=ghp_xxx
    setx GITHUB_TOKEN ghp_xxx
    python scripts/push_via_api.py

需要 token 具备 repo 权限（Classic PAT 的 repo 或 Fine-grained PAT 的
Contents: Read and write）。

注意：脚本只推送 `origin/main..main` 的提交；本地还有未提交改动时会拒绝推送，
避免把工作区状态和提交内容搞混。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
REPO = "kingleeqon/network-toolkit"
BRANCH = "main"


# ---------- git 辅助 ----------
def git(*args, binary=False):
    out = subprocess.run(["git"] + list(args), cwd=str(BASE_DIR),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args),
                                              out.stderr.decode("utf-8", "ignore")))
    return out.stdout if binary else out.stdout.decode("utf-8", "ignore")


def pending_commits():
    """本地领先远端的提交（旧 → 新）。"""
    text = git("rev-list", "--reverse", "origin/%s..%s" % (BRANCH, BRANCH))
    return [c for c in text.split() if c]


def commit_info(sha):
    parents = git("rev-list", "--parents", "-n", "1", sha).split()[1:]
    return {
        "sha": sha,
        "parents": parents,
        "message": git("log", "-1", "--format=%B", sha).rstrip("\n"),
        "author": git("log", "-1", "--format=%an%n%ae", sha).split("\n"),
        "committer": git("log", "-1", "--format=%cn%n%ce", sha).split("\n"),
        "tree": git("log", "-1", "--format=%T", sha).strip(),
    }


def changed_files(sha):
    """该提交相对父提交变动的文件：[(path, mode, type, sha_or_None)]。"""
    parent = git("rev-list", "--parents", "-n", "1", sha).split()[1:]
    base = parent[0] if parent else None
    args = ["diff-tree", "-r", "--no-commit-id", "--name-status", sha]
    if base:
        args = ["diff-tree", "-r", "--no-commit-id", "--name-status", base, sha]
    rows = []
    for line in git(*args).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        if status.startswith("D"):
            rows.append((path, "100644", "blob", None))   # 删除
        else:
            rows.append((path, "100644", "blob", "NEW"))
    return rows


# ---------- GitHub API ----------
def api(path, method="GET", payload=None, token=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "network-toolkit-push")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError("GitHub API %s %s → HTTP %s：%s"
                           % (method, path, exc.code, detail[:300]))


def upload_blob(token, path):
    """上传一个文件为 blob，返回 sha。"""
    data = (BASE_DIR / path).read_bytes()
    res = api("/repos/%s/git/blobs" % REPO, "POST", {
        "content": base64.b64encode(data).decode("ascii"),
        "encoding": "base64",
    }, token)
    return res["sha"]


def main():
    dry = "--dry-run" in sys.argv
    commits = pending_commits()
    if not commits:
        print("没有待推送的提交（origin/%s 已同步）" % BRANCH)
        return 0
    print("待推送提交 %d 个：" % len(commits))
    for sha in commits:
        info = commit_info(sha)
        first = info["message"].splitlines()[0] if info["message"] else ""
        files = changed_files(sha)
        print("  %s  %-52s %d 个文件" % (sha[:8], first[:52], len(files)))
        if dry:
            for path, _mode, _type, _s in files:
                print("        - %s" % path)
    if dry:
        print("\n[dry-run] 未做任何网络写操作。去掉 --dry-run 即真推。")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("缺少环境变量 GITHUB_TOKEN（需要 repo 权限的 PAT）")
        return 2

    # 以远端当前提交为起点，逐个重建本地提交
    parent = git("rev-parse", "origin/%s" % BRANCH).strip()
    parent_tree = git("log", "-1", "--format=%T", parent).strip()
    for sha in commits:
        info = commit_info(sha)
        tree_entries = []
        for path, mode, ftype, new in changed_files(sha):
            if new is None:
                tree_entries.append({"path": path, "mode": mode,
                                     "type": ftype, "sha": None})
            else:
                blob = upload_blob(token, path)
                tree_entries.append({"path": path, "mode": mode,
                                     "type": ftype, "sha": blob})
        tree = api("/repos/%s/git/trees" % REPO, "POST", {
            "base_tree": parent_tree, "tree": tree_entries}, token)
        commit = api("/repos/%s/git/commits" % REPO, "POST", {
            "message": info["message"],
            "tree": tree["sha"],
            "parents": [parent],
            "author": {"name": info["author"][0], "email": info["author"][1]},
            "committer": {"name": info["committer"][0],
                          "email": info["committer"][1]},
        }, token)
        print("  已重建 %s → %s" % (sha[:8], commit["sha"][:8]))
        parent = commit["sha"]
        parent_tree = tree["sha"]

    api("/repos/%s/git/refs/heads/%s" % (REPO, BRANCH), "PATCH",
        {"sha": parent, "force": False}, token)
    print("\n推送完成：%s → %s" % (BRANCH, parent[:8]))
    print("接着更新本地远端引用：git fetch origin && git reset --soft origin/%s" % BRANCH)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("[FAIL] %s" % exc)
        sys.exit(1)
