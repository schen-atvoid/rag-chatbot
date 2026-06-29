#!/usr/bin/env python3
"""
DeepSeek Interactive Assistant — native tool calling agent.

Used by .github/workflows/claude.yml (comment-only) and
.github/workflows/auto-fix.yml (write + commit + open PR).

Reads env:
  DEEPSEEK_API_KEY   — required
  GITHUB_TOKEN       — required
  GITHUB_REPOSITORY  — required
  GITHUB_EVENT_NAME  — required
  GITHUB_EVENT_PATH  — required
  MODEL_NAME         — default "deepseek-v4-flash"
  MAX_ROUNDS         — default 5
  MODE               — "comment" (default) | "auto-fix"
  ISSUE_NUMBER       — for auto-fix: which issue to fix
  PR_BRANCH          — for auto-fix: branch name to create
"""
import os
import json
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ==================== Config ====================
api_key = os.environ["DEEPSEEK_API_KEY"]
gh_token = os.environ["GITHUB_TOKEN"]
model_name = os.environ.get("MODEL_NAME", "deepseek-v4-flash")
repo = os.environ["GITHUB_REPOSITORY"]
event_name = os.environ["GITHUB_EVENT_NAME"]
max_rounds = int(os.environ.get("MAX_ROUNDS", "5"))
mode = os.environ.get("MODE", "comment")  # "comment" or "auto-fix"
issue_number = os.environ.get("ISSUE_NUMBER", "")
pr_branch = os.environ.get("PR_BRANCH", "")

# ==================== Parse event ====================
event_path = os.environ["GITHUB_EVENT_PATH"]
with open(event_path) as f:
    event = json.load(f)

user_query = ""
pr_info = None
comments_url = None
context_label = ""

if event_name == "issue_comment":
    user_query = event.get("comment", {}).get("body", "")
    issue = event.get("issue", {})
    comments_url = issue.get("comments_url")
    is_pr = "pull_request" in issue
    n = issue.get("number")
    context_label = "PR #" + str(n) if is_pr else "Issue #" + str(n)
    if is_pr:
        pr_info = {"base": {"sha": "HEAD~1"}, "head": {"sha": "HEAD"}}
elif event_name == "pull_request_review_comment":
    user_query = event.get("comment", {}).get("body", "")
    pr_info = event.get("pull_request")
    if pr_info:
        n = pr_info.get("number")
        comments_url = "https://api.github.com/repos/" + repo + "/issues/" + str(n) + "/comments"
        context_label = "PR #" + str(n)
elif event_name == "issues":
    issue = event.get("issue", {})
    user_query = (issue.get("body", "") + "\n\n" + issue.get("title", "")).strip()
    comments_url = issue.get("comments_url")
    context_label = "Issue #" + str(issue.get("number", "?"))
elif event_name == "pull_request_review":
    user_query = event.get("review", {}).get("body", "")
    pr_info = event.get("pull_request")
    if pr_info:
        n = pr_info.get("number")
        comments_url = "https://api.github.com/repos/" + repo + "/issues/" + str(n) + "/comments"
        context_label = "PR #" + str(n)

user_query = re.sub(r"@claude\b", "", user_query, flags=re.IGNORECASE).strip()
if not user_query:
    print("[skip] Empty query after stripping @claude")
    sys.exit(0)

# ==================== Tool schemas (OpenAI/DeepSeek compatible) ====================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file. Truncated to 50KB. Use this FIRST before guessing file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path relative to repo root, e.g. 'src/main.py'"}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a read-only shell command and return stdout. Allowed prefixes: git, ls, find, cat, head, tail, grep, rg, wc, stat, file, tree, diff, echo, pytest, npm, pnpm, yarn, python, node. Refuses destructive operators.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files in the repository matching a glob pattern (e.g. '*.py', 'test_*.js').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory (ls -la).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path", "default": "."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_pr_diff",
            "description": "Get the git diff of recent commits (HEAD~1..HEAD). Only meaningful in PR context.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

# In auto-fix mode, add write tools
if mode == "auto-fix":
    TOOLS.extend([
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file (creates or overwrites). Only available in auto-fix mode. Use this to apply your code changes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path relative to repo root"},
                        "content": {"type": "string", "description": "Full new file content"}
                    },
                    "required": ["file_path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": "Apply a unified diff patch to a file. Useful for small targeted changes. Only available in auto-fix mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path relative to repo root"},
                        "patch": {"type": "string", "description": "Unified diff format patch"}
                    },
                    "required": ["file_path", "patch"]
                }
            }
        },
    ])

# ==================== Tool implementations ====================
def tool_read_file(file_path):
    try:
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "error": "File not found: " + file_path}
        if not p.is_file():
            return {"success": False, "error": "Not a file: " + file_path}
        content = p.read_text(encoding="utf-8", errors="replace")
        original_size = len(content)
        if original_size > 50000:
            content = content[:50000] + "\n... (truncated, file > 50KB)"
        return {"success": True, "content": content, "size": original_size}
    except Exception as e:
        return {"success": False, "error": str(e)}

SAFE_PREFIXES = (
    "git", "ls", "find", "cat", "head", "tail", "grep", "rg",
    "wc", "stat", "file", "tree", "diff", "echo",
    "pytest", "npm", "pnpm", "yarn", "python", "node",
)
FORBIDDEN_TOKENS = (
    "rm ", "rm\t", "rmdir", "mv ", "cp ", ">",
    "|", "&&", "||", ";", "`", "$(",
)

def tool_run_command(command, timeout=30):
    stripped = command.strip()
    first = stripped.split()[0] if stripped else ""
    if not any(first == p or stripped.startswith(p + " ") for p in SAFE_PREFIXES):
        return {"success": False, "error": "Command '" + first + "' not in safe allow-list"}
    bad = [t for t in FORBIDDEN_TOKENS if t in stripped]
    if bad:
        return {"success": False, "error": "Command contains forbidden tokens: " + str(bad)}
    try:
        result = subprocess.run(
            stripped, shell=True, capture_output=True,
            text=True, timeout=timeout, cwd=os.getcwd()
        )
        out = result.stdout
        if result.stderr:
            out += "\n[STDERR]\n" + result.stderr
        if not out:
            out = "(no output)"
        if len(out) > 30000:
            out = out[:30000] + "\n... (truncated)"
        return {
            "success": result.returncode == 0,
            "output": out,
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out after " + str(timeout) + "s"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def tool_search_files(pattern):
    try:
        result = subprocess.run(
            ["find", ".", "-name", pattern, "-not", "-path", "./.git/*"],
            capture_output=True, text=True, timeout=30
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        return {
            "success": True,
            "files": files[:50],
            "count": len(files),
            "truncated": len(files) > 50
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def tool_list_directory(path="."):
    try:
        result = subprocess.run(
            ["ls", "-la", path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip() or "ls failed"}
        return {"success": True, "output": result.stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}

def tool_get_pr_diff():
    if not pr_info:
        return {"success": False, "error": "Not in a PR context (no pull_request in event)"}
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1..HEAD"],
            capture_output=True, text=True, timeout=60
        )
        diff = result.stdout
        if not diff:
            # Try merge-base fallback
            mb = subprocess.run(
                ["git", "merge-base", "origin/HEAD", "HEAD"],
                capture_output=True, text=True
            )
            if mb.returncode == 0 and mb.stdout.strip():
                result = subprocess.run(
                    ["git", "diff", mb.stdout.strip() + "..HEAD"],
                    capture_output=True, text=True, timeout=60
                )
                diff = result.stdout
        if len(diff) > 100000:
            diff = diff[:100000] + "\n... (diff truncated, > 100KB)"
        return {
            "success": True,
            "diff": diff or "(empty diff)",
            "size": len(diff)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# Auto-fix mode write tools
def tool_write_file(file_path, content):
    if mode != "auto-fix":
        return {"success": False, "error": "write_file is only available in auto-fix mode"}
    try:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"success": True, "path": file_path, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def tool_apply_patch(file_path, patch):
    if mode != "auto-fix":
        return {"success": False, "error": "apply_patch is only available in auto-fix mode"}
    try:
        # Use git apply
        result = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"success": False, "error": "Patch check failed: " + result.stderr}
        # Apply for real
        result = subprocess.run(
            ["git", "apply", "-"],
            input=patch, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"success": False, "error": "Patch apply failed: " + result.stderr}
        return {"success": True, "path": file_path}
    except Exception as e:
        return {"success": False, "error": str(e)}

TOOL_DISPATCH = {
    "read_file":      lambda args: tool_read_file(args["file_path"]),
    "run_command":    lambda args: tool_run_command(args["command"], args.get("timeout", 30)),
    "search_files":   lambda args: tool_search_files(args["pattern"]),
    "list_directory": lambda args: tool_list_directory(args.get("path", ".")),
    "get_pr_diff":    lambda args: tool_get_pr_diff(),
    "write_file":     lambda args: tool_write_file(args["file_path"], args["content"]),
    "apply_patch":    lambda args: tool_apply_patch(args["file_path"], args["patch"]),
}

# ==================== DeepSeek API ====================
def call_deepseek(messages, tools=None):
    url = "https://api.deepseek.com/chat/completions"
    data = {
        "model": model_name,
        "messages": messages,
        "stream": False,
        "temperature": 0.3,
    }
    if tools:
        data["tools"] = tools
        data["tool_choice"] = "auto"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            }
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_error": "HTTP " + str(e.code) + ": " + body[:1000]}
    except urllib.error.URLError as e:
        return {"_error": "URLError: " + str(e)}
    except Exception as e:
        return {"_error": "Exception: " + str(e)}

# ==================== System Prompt ====================
def build_system_prompt():
    # Build the prompt as a list of lines, then join.
    # This avoids issues with markdown special chars being interpreted as YAML tokens
    # when the prompt was embedded in YAML.
    lines = [
        "You are DeepSeek Assistant, a code-fixing agent running inside GitHub Actions.",
        "",
        "## Context",
        "Repository: " + repo,
        "Event: " + event_name,
        "Target: " + context_label,
        "Mode: " + mode,
        "",
    ]
    lines.append("## Tools")
    lines.append("You have 5 read-only tools" + (" + 2 write tools (auto-fix mode)" if mode == "auto-fix" else "") + ":")
    lines.append("1. read_file(file_path) -- read file contents (use FIRST before guessing)")
    lines.append("2. run_command(command) -- run safe shell command (allow-listed prefixes only)")
    lines.append("3. search_files(pattern) -- find files by glob")
    lines.append("4. list_directory(path) -- list a directory")
    lines.append("5. get_pr_diff() -- diff vs HEAD~1 (PR context only)")
    if mode == "auto-fix":
        lines.append("6. write_file(file_path, content) -- write full new file content (auto-fix only)")
        lines.append("7. apply_patch(file_path, patch) -- apply unified diff (auto-fix only)")
    lines.append("")
    lines.append("## Workflow")
    lines.append("1. Use tools to gather information FIRST (don't guess file contents)")
    lines.append("2. After investigation:")
    if mode == "auto-fix":
        lines.append("   - For 'fix/implement' requests: USE write_file/apply_patch to MAKE the changes")
        lines.append("   - Provide a summary of what you changed and why")
    else:
        lines.append("   - For 'explain/review' requests: Markdown explanation with file:line references")
        lines.append("   - For 'fix/implement' requests: provide EXACT code changes in ```diff or ```suggestion blocks")
        lines.append("   - Note: this mode is comment-only; auto-fix workflow will apply your suggestions")
    lines.append("3. Be concise but complete; show reasoning briefly")
    lines.append("")
    lines.append("## CRITICAL constraints")
    if mode == "auto-fix":
        lines.append("- You CAN modify files using write_file / apply_patch")
        lines.append("- Make MINIMAL, SURGICAL changes (don't refactor unrelated code)")
        lines.append("- Add/run tests if the project has them")
        lines.append("- Don't change unrelated files")
    else:
        lines.append("- This workflow is comment-only: you CANNOT modify files")
        lines.append("- When user asks to 'fix' something, provide complete code patches (don't say 'I would change X')")
    lines.append("- Never invent file contents -- always read_file first")
    lines.append("- Prefer get_pr_diff() over re-reading many files in PR context")
    lines.append("")
    lines.append("## Response style")
    lines.append("- Use Chinese if the user wrote Chinese, English if English")
    lines.append("- Format code in triple-backtick blocks with language hints")
    lines.append("- Reference files as path/to/file.py:LINE when relevant")
    return "\n".join(lines)

system_prompt = build_system_prompt()

# ==================== Main loop ====================
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_query}
]

print("=== DeepSeek Agent ===")
print("Context: " + context_label)
print("Mode: " + mode)
print("Query (" + str(len(user_query)) + " chars): " + user_query[:200] + ("..." if len(user_query) > 200 else ""))

final_text = ""
tool_log = []

for round_num in range(max_rounds):
    print("\n--- Round " + str(round_num + 1) + "/" + str(max_rounds) + " ---")
    response = call_deepseek(messages, tools=TOOLS)

    if "_error" in response:
        err = response["_error"]
        print("[api-error] " + err)
        final_text = "[API error]\n\n```\n" + err + "\n```"
        break

    try:
        msg = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        final_text = "[unexpected response]\n```json\n" + json.dumps(response, indent=2)[:2000] + "\n```"
        break

    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []

    if content.strip():
        preview = content[:300] + ("..." if len(content) > 300 else "")
        print("[model] " + preview)

    if round_num == 0 and not tool_calls:
        final_text = content

    if not tool_calls:
        if content.strip() and round_num > 0:
            final_text = content
        print("[done] Model gave final answer (no tool calls)")
        break

    messages.append({
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls
    })

    for tc in tool_calls:
        fn_name = tc["function"]["name"]
        raw_args = tc["function"].get("arguments") or "{}"
        try:
            fn_args = json.loads(raw_args)
        except json.JSONDecodeError as e:
            result = {"success": False, "error": "Bad tool arguments JSON: " + str(e)}
            fn_args = {}

        if fn_name not in TOOL_DISPATCH:
            result = {"success": False, "error": "Unknown tool: " + fn_name}
        elif "error" not in locals() or result.get("success", True) is not False or "error" in result:
            # Try to call the tool
            try:
                result = TOOL_DISPATCH[fn_name](fn_args)
            except Exception as e:
                result = {"success": False, "error": "Tool execution failed: " + str(e)}

        args_preview = json.dumps(fn_args, ensure_ascii=False)[:120]
        print("[tool] " + fn_name + "(" + args_preview + ("..." if len(args_preview) >= 120 else "") + ")")
        if result.get("success"):
            print("       -> OK")
        else:
            print("       -> FAIL: " + str(result.get("error", "unknown")))

        tool_log.append({"tool": fn_name, "args": fn_args, "result": result})
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps(result, ensure_ascii=False)
        })
else:
    print("[warn] Reached max_rounds=" + str(max_rounds) + ", stopping")

# ==================== Post comment / save summary ====================
if mode == "auto-fix":
    # Write tool log and final text to files for next steps
    summary_path = os.environ.get("SUMMARY_PATH", "/tmp/deepseek_summary.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("# DeepSeek Auto-Fix Summary\n\n")
        f.write("**Context**: " + context_label + "\n")
        f.write("**Mode**: auto-fix\n")
        f.write("**Model**: " + model_name + "\n\n")
        f.write("## Final Response\n\n")
        f.write(final_text or "(no final text)")
        f.write("\n\n## Tool Calls\n\n")
        for entry in tool_log:
            f.write("- **" + entry["tool"] + "**(" + json.dumps(entry["args"], ensure_ascii=False)[:200] + ")")
            f.write(" -> " + ("OK" if entry["result"].get("success") else "FAIL: " + str(entry["result"].get("error", ""))) + "\n")
    print("[summary] Wrote " + summary_path)
    print("=== Done (auto-fix mode) ===")
    sys.exit(0)

# comment mode
if not comments_url:
    print("[no-comments-url] Cannot post comment")
    sys.exit(0)

if tool_log:
    log_section = "\n\n---\n\n### Tool calls\n\n"
    for entry in tool_log:
        args_s = json.dumps(entry["args"], ensure_ascii=False)
        if len(args_s) > 100:
            args_s = args_s[:100] + "..."
        status = "OK" if entry["result"].get("success") else "FAIL"
        log_section += "- [" + status + "] `" + entry["tool"] + "(" + args_s + ")`\n"
    final_text = (final_text or "(no content)") + log_section

comment_body = "## DeepSeek V4 Assistant\n\n" + (final_text or "(no content)") + "\n\n---\n*Powered by DeepSeek V4 Flash / native tool calling / " + str(len(TOOLS)) + " tools*\n"

req = urllib.request.Request(
    comments_url,
    data=json.dumps({"body": comment_body}, ensure_ascii=False).encode("utf-8"),
    headers={
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "token " + gh_token,
        "Accept": "application/vnd.github.v3+json"
    }
)
try:
    with urllib.request.urlopen(req) as resp:
        print("[comment] Posted (HTTP " + str(resp.getcode()) + ")")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")[:500]
    print("[comment-error] HTTP " + str(e.code) + ": " + body)
except Exception as e:
    print("[comment-error] " + str(e))

print("=== Done ===")
