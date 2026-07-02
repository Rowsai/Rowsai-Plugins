import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_DIR = Path("plugins")
OUTPUT_FILE = Path("pluginmaster.json")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

RELEASE_ASSET_NAME = "latest.zip"

DEFAULT_AUTHOR = "Rowsai"
DEFAULT_GITHUB_OWNER = "Rowsai"
DEFAULT_BRANCH = "main"


PLUGINMASTER_KEYS = [
    "Author",
    "Name",
    "Punchline",
    "Description",
    "Changelog",
    "InternalName",
    "AssemblyVersion",
    "RepoUrl",
    "ApplicableVersion",
    "Tags",
    "DalamudApiLevel",
    "IconUrl",
    "ImageUrls",
    "DownloadLinkInstall",
    "DownloadLinkTesting",
    "DownloadLinkUpdate",
    "IsHide",
    "IsTestingExclusive",
    "DownloadCount",
    "LastUpdate",
]


def main():
    manifests = load_plugin_manifests()
    pluginmaster = []

    for manifest_path, manifest in manifests:
        normalized = normalize_manifest(manifest_path, manifest)
        pluginmaster.append(normalized)

    pluginmaster.sort(key=lambda x: x.get("Name", "").lower())

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(pluginmaster, f, indent=4, ensure_ascii=False)
        f.write("\n")

    print(f"Generated: {OUTPUT_FILE}")
    print(f"Plugin count: {len(pluginmaster)}")


def load_plugin_manifests():
    if not PLUGIN_DIR.exists():
        raise FileNotFoundError(f"{PLUGIN_DIR} が存在しません。")

    manifests = []

    for plugin_folder in sorted(PLUGIN_DIR.iterdir()):
        if not plugin_folder.is_dir():
            continue

        # 基本形: plugins/HappyTrigger/HappyTrigger.json
        expected_json = plugin_folder / f"{plugin_folder.name}.json"

        # ファイル名がフォルダ名と違う場合も拾う
        json_files = sorted(plugin_folder.glob("*.json"))

        if expected_json.exists():
            json_path = expected_json
        elif json_files:
            json_path = json_files[0]
            print(f"Warning: {expected_json} が無いため {json_path} を使用します。")
        else:
            print(f"Skip: {plugin_folder} に json がありません。")
            continue

        with json_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        manifests.append((json_path, manifest))

    return manifests


def normalize_manifest(manifest_path, manifest):
    plugin = dict(manifest)

    internal_name = plugin.get("InternalName") or plugin.get("Name") or manifest_path.parent.name
    name = plugin.get("Name") or internal_name
    author = plugin.get("Author") or DEFAULT_AUTHOR

    plugin["Author"] = author
    plugin["Name"] = name
    plugin["InternalName"] = internal_name

    plugin.setdefault("Punchline", name)
    plugin.setdefault("Description", plugin["Punchline"])
    plugin.setdefault("ApplicableVersion", "any")
    plugin.setdefault("Tags", [])
    plugin.setdefault("IsHide", False)
    plugin.setdefault("IsTestingExclusive", False)

    if "AssemblyVersion" not in plugin:
        plugin["AssemblyVersion"] = "0.0.0.1"
        print(f"Warning: {internal_name} に AssemblyVersion が無いため 0.0.0.1 を設定しました。")

    if "DalamudApiLevel" not in plugin:
        plugin["DalamudApiLevel"] = 15
        print(f"Warning: {internal_name} に DalamudApiLevel が無いため 15 を設定しました。")

    repo_url = plugin.get("RepoUrl")

    if not repo_url:
        repo_url = infer_repo_url(author, internal_name)
        plugin["RepoUrl"] = repo_url
        print(f"Info: {internal_name} の RepoUrl を自動補完しました: {repo_url}")

    owner, repo = parse_github_repo(repo_url)

    if owner and repo:
        download_url = f"https://github.com/{owner}/{repo}/releases/latest/download/{RELEASE_ASSET_NAME}"

        plugin["DownloadLinkInstall"] = download_url
        plugin["DownloadLinkTesting"] = download_url
        plugin["DownloadLinkUpdate"] = download_url

        plugin.setdefault("Changelog", f"https://github.com/{owner}/{repo}/releases")

        if not plugin.get("IconUrl"):
            plugin["IconUrl"] = f"https://raw.githubusercontent.com/{owner}/{repo}/{DEFAULT_BRANCH}/images/icon.png"
            print(f"Info: {internal_name} の IconUrl を自動補完しました。")

        download_count, last_update = fetch_release_info(owner, repo)
        plugin["DownloadCount"] = download_count
        plugin["LastUpdate"] = str(last_update or int(time.time()))
    else:
        print(f"Warning: {internal_name} の RepoUrl を解析できません: {repo_url}")

        plugin.setdefault("DownloadCount", 0)
        plugin.setdefault("LastUpdate", str(int(time.time())))

    return keep_pluginmaster_keys(plugin)


def infer_repo_url(author, internal_name):
    """
    RepoUrl が無い場合の補完ルール。

    例:
      Author=Rowsai, InternalName=devLibra
      -> https://github.com/Rowsai/devLibra
    """

    owner = DEFAULT_GITHUB_OWNER

    if author and re.fullmatch(r"[A-Za-z0-9_.-]+", author):
        owner = author

    return f"https://github.com/{owner}/{internal_name}"


def parse_github_repo(repo_url):
    if not repo_url:
        return None, None

    repo_url = repo_url.strip()

    patterns = [
        r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
    ]

    for pattern in patterns:
        match = re.match(pattern, repo_url)
        if match:
            return match.group(1), match.group(2)

    return None, None


def fetch_release_info(owner, repo):
    """
    GitHub Releases から latest.zip の download_count と latest release の更新日時を取得する。
    失敗しても pluginmaster 生成自体は止めない。
    """

    url = f"https://api.github.com/repos/{owner}/{repo}/releases"

    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "Rowsai-Plugins-Generator")

    if GITHUB_TOKEN:
        request.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Warning: {owner}/{repo} の release 情報取得に失敗しました: HTTP {e.code}")
        return 0, 0
    except urllib.error.URLError as e:
        print(f"Warning: {owner}/{repo} の release 情報取得に失敗しました: {e}")
        return 0, 0
    except TimeoutError:
        print(f"Warning: {owner}/{repo} の release 情報取得がタイムアウトしました。")
        return 0, 0

    download_count = 0
    last_update = 0

    for release in releases:
        published_at = release.get("published_at")

        if published_at:
            timestamp = parse_github_datetime(published_at)
            if timestamp:
                last_update = max(last_update, timestamp)

        for asset in release.get("assets", []):
            if asset.get("name") == RELEASE_ASSET_NAME:
                download_count += int(asset.get("download_count", 0))

    return download_count, last_update


def parse_github_datetime(value):
    try:
        return int(
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except ValueError:
        return 0


def keep_pluginmaster_keys(plugin):
    result = {}

    for key in PLUGINMASTER_KEYS:
        if key in plugin:
            result[key] = plugin[key]

    return result


if __name__ == "__main__":
    main()