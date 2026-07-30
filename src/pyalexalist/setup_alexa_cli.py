"""
Downloads the binary build of alexa-cookie-cli from https://github.com/adn77/alexa-cookie-cli,
which is based on https://github.com/Apollon77/alexa-cookie.
"""
import argparse
import sys
import platform
import requests
from pathlib import Path

GITHUB_API_BASE   = "https://api.github.com/repos/adn77/alexa-cookie-cli"
GITHUB_LATEST_API = f"{GITHUB_API_BASE}/releases/latest"
GITHUB_TAGS_API   = f"{GITHUB_API_BASE}/releases"

PLATFORM_KEY = {
    "windows": "win",
    "linux":   "linux",
    "darwin":  "macos",
}


def get_platform_key() -> str:
    system = platform.system().lower()
    key = PLATFORM_KEY.get(system)
    if key is None:
        print(f"Unsupported platform: {system}")
        sys.exit(1)
    return key


def fetch_release(version: str | None) -> dict:
    """Fetch release metadata for a specific version tag, or the latest release."""
    if version:
        tag = version if version.startswith("v") else f"v{version}"
        url = f"{GITHUB_API_BASE}/releases/tags/{tag}"
        print(f"Fetching release info for {tag} ...")
    else:
        url = GITHUB_LATEST_API
        print("Fetching latest release info from GitHub...")
    resp = requests.get(url, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code == 404:
        print(f"Release '{version}' not found. Use --list to see available versions.")
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def list_versions() -> None:
    """Print all available release versions with their description and assets."""
    print("Fetching available releases...")
    resp = requests.get(GITHUB_TAGS_API, headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    releases = resp.json()
    for release in releases:
        tag = release["tag_name"]
        label = f"{tag} (latest)" if not release.get("prerelease") and release == releases[0] else tag
        body = (release.get("body") or "").strip()
        assets = [a["name"] for a in release["assets"] if not a["name"].endswith((".gz", ".zip"))]
        print(f"\n  {label}")
        if body:
            for line in body.splitlines():
                print(f"    {line}")
        print(f"    Assets: {', '.join(assets)}")


def download(version: str | None, force: bool, binary_dir: Path | None = None) -> Path:
    if binary_dir is None:
        binary_dir = Path.cwd() / "alexa-cookie-cli"

    key = get_platform_key()
    suffix = ".exe" if key == "win" else ""
    binary = binary_dir / f"alexa-cookie-cli-{key}{suffix}"

    if binary.exists() and not force:
        print(f"{binary.name} already present, skipping download. Use --force to re-download.")
        return binary

    release = fetch_release(version)
    print(f"Release: {release['tag_name']}")

    asset = next((a for a in release["assets"] if key in a["name"].lower()), None)
    if asset is None:
        print(f"Could not find a {key} asset in release {release['tag_name']}. Assets available:")
        for a in release["assets"]:
            print(f"  {a['name']}")
        sys.exit(1)

    binary_dir.mkdir(exist_ok=True)

    print(f"Downloading {asset['name']} ...")
    with requests.get(asset["browser_download_url"], stream=True) as dl:
        dl.raise_for_status()
        binary.write_bytes(b"".join(dl.iter_content(chunk_size=8192)))

    binary.chmod(binary.stat().st_mode | 0o111)  # ensure executable on linux/macos
    print(f"Saved as {binary} ({binary.stat().st_size:,} bytes)")
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download the alexa-cookie-cli binary.")
    parser.add_argument(
        "--version", "-v",
        metavar="VERSION",
        help="release version to download (e.g. 5.0.1); defaults to latest",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="list all available release versions and exit",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="re-download even if binary already exists",
    )
    args = parser.parse_args()

    if args.list:
        list_versions()
    else:
        download(version=args.version, force=args.force)
