"""Published maps: GitHub Release assets + the committed manifest.

A map (database + routing tiles) worth keeping is published as a release of
the e39-rpi-maps repository, tagged map-<built_at>, with the files as
release assets (no size or bandwidth quota, unlike Git LFS; each file must
be under 2 GiB). map-release.json in the repository root (committed) pins
which published map the image ships: tag, files, sizes, SHA-256. Fresh
clones and build machines fetch exactly that map and check it.

Standard library only (+ mapdata.schema): used by the toolkit (publish,
fetch), scripts/fetch-map.py on build machines, and the Yocto data recipe
(checks the image's map against the manifest).

Authentication: the repository is private, so every call needs a token:
GH_TOKEN / GITHUB_TOKEN, else `gh auth token` (log in once with
`gh auth login`).
"""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import schema

DEFAULT_REPO = "linster/e39-rpi-maps"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
MANIFEST_NAME = "map-release.json"
MAX_ASSET = 2 * 1024 ** 3  # GitHub's per-file limit for release assets


class ReleaseError(Exception):
    pass


# --- GitHub API ------------------------------------------------------------

def token():
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    gh = shutil.which("gh") or os.path.expanduser("~/.local/bin/gh")
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=20)
    except OSError:
        raise ReleaseError("not logged in to GitHub: install gh and run `gh auth login` "
                           "(or set GH_TOKEN)") from None
    if out.returncode != 0 or not out.stdout.strip():
        raise ReleaseError("not logged in to GitHub: run `gh auth login`")
    return out.stdout.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Asset downloads redirect to a signed storage URL, which must be fetched
    # *without* our Authorization header (it would be rejected, or leak the
    # token to another host): handle redirects ourselves.
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class GitHub:

    def __init__(self, repo=DEFAULT_REPO, api=API, uploads=UPLOADS, tok=None):
        self.repo, self.api, self.uploads = repo, api.rstrip("/"), uploads.rstrip("/")
        self._token = tok

    @property
    def auth(self):
        if self._token is None:
            self._token = token()
        return {"Authorization": f"Bearer {self._token}", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "e39maps-toolkit"}

    def _call(self, method, url, body=None, headers=None, raw=False, timeout=60):
        data = json.dumps(body).encode() if body is not None and not hasattr(body, "read") else body
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Accept": "application/vnd.github+json", **self.auth,
                                              **({"Content-Type": "application/json"} if body is not None
                                                 and not hasattr(body, "read") else {}),
                                              **(headers or {})})
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                return exc.headers["Location"]
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 401:
                raise ReleaseError("GitHub refused the token: run `gh auth login` again") from None
            if exc.code == 404:
                raise ReleaseError(f"not found on GitHub: {url} (no access to {self.repo}?)") from None
            raise ReleaseError(f"GitHub: HTTP {exc.code} for {method} {url}: {detail}") from None
        with resp:
            return resp if raw else json.loads(resp.read() or b"null")

    def release_by_tag(self, tag):
        try:
            return self._call("GET", f"{self.api}/repos/{self.repo}/releases/tags/{tag}")
        except ReleaseError as exc:
            if "not found" in str(exc):
                return None
            raise

    def list_releases(self):
        """Published maps (tags map-*), newest first."""
        found, page = [], 1
        while True:
            batch = self._call("GET", f"{self.api}/repos/{self.repo}/releases?per_page=100&page={page}")
            found += [r for r in batch if r["tag_name"].startswith("map-")]
            if len(batch) < 100:
                break
            page += 1
        return sorted(found, key=lambda r: r["tag_name"], reverse=True)

    def commit_exists(self, sha):
        try:
            self._call("GET", f"{self.api}/repos/{self.repo}/commits/{sha}")
            return True
        except ReleaseError:
            return False

    def create_release(self, tag, target, title, notes):
        return self._call("POST", f"{self.api}/repos/{self.repo}/releases", {
            "tag_name": tag, "target_commitish": target, "name": title, "body": notes,
            "draft": False, "prerelease": False})

    def delete_release(self, release_id):
        self._call("DELETE", f"{self.api}/repos/{self.repo}/releases/{release_id}")

    def upload(self, release, path, progress=None):
        path = Path(path)
        size = path.stat().st_size

        class Reader:
            def __init__(self, f):
                self.f, self.done = f, 0

            def read(self, n=-1):
                chunk = self.f.read(1 << 20 if n is None or n < 0 else n)
                self.done += len(chunk)
                if progress:
                    progress(path.name, self.done, size)
                return chunk

        with open(path, "rb") as f:
            return self._call(
                "POST", f"{self.uploads}/repos/{self.repo}/releases/{release['id']}/assets?name={path.name}",
                body=Reader(f), headers={"Content-Type": "application/octet-stream",
                                         "Content-Length": str(size)}, timeout=3600)

    def download(self, asset, dest, progress=None):
        url = f"{self.api}/repos/{self.repo}/releases/assets/{asset['id']}"
        location = self._call("GET", url, headers={"Accept": "application/octet-stream"}, raw=True)
        req = (urllib.request.Request(location, headers={"User-Agent": "e39maps-toolkit"})
               if isinstance(location, str) else None)
        resp = urllib.request.urlopen(req, timeout=60) if req else location
        size, done = int(asset.get("size", 0)), 0
        with resp, open(dest, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(asset["name"], done, size)


# --- manifest ----------------------------------------------------------------

def manifest_path(repo_root):
    return Path(repo_root) / MANIFEST_NAME


def read_manifest(repo_root):
    path = manifest_path(repo_root)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def tag_for(meta):
    """map-<region slug>-<built_at>, e.g. map-ottawa-20261113T040254Z (maps of
    different regions side by side); older maps without a slug:
    map-<built_at>."""
    stamp = meta["built_at"].replace("-", "").replace(":", "")
    slug = meta.get("region_slug")
    return f"map-{slug}-{stamp}" if slug else f"map-{stamp}"


def title_for(meta):
    label = meta.get("region_label") or meta.get("region", "")
    radius = meta.get("radius_km")
    return f"Map: {label}" + (f" ({radius} km)" if radius else "") + f", {meta['built_at'][:10]}"


def should_pin(meta, repo_root):
    """Pin a newly published map for image builds? Yes when nothing is pinned
    yet or the pinned map is of the same region (same slug); a map of another
    region (Edmonton while the car's image ships Ottawa) only when asked.
    A pinned map from before maps had names (no slug) is replaced: there was
    only one region then."""
    pinned = read_manifest(repo_root)
    if pinned is None or not pinned.get("region_slug"):
        return True
    return bool(meta.get("region_slug")) and pinned.get("region_slug") == meta.get("region_slug")


def _file_entry(path, sha256=None):
    path = Path(path)
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256 or schema.sha256_file(path)}


# --- publish / fetch ---------------------------------------------------------

def publish(db_path, repo_root, gh=None, log=print, progress=None, target=None, pin=None):
    """Publish the map at db_path as a release. pin: write it to
    map-release.json (the map image builds ship); None = should_pin().
    Returns the release's manifest (dict)."""
    db_path = Path(db_path)
    meta = schema.check(db_path)
    routing = schema.routing_info(meta)
    pairs = schema.check_companions(meta, db_path.parent, verify_hash=False)
    files = [db_path] + [path for _c, path in pairs]
    attribution = db_path.with_name("ATTRIBUTION.txt")
    if attribution.exists():
        files.append(attribution)
    for f in files:
        if f.stat().st_size >= MAX_ASSET:
            raise ReleaseError(f"{f.name} is {f.stat().st_size / 1e9:.1f} GB; GitHub release assets must be under 2 GiB")

    gh = gh or GitHub()
    tag = tag_for(meta)
    if gh.release_by_tag(tag):
        raise ReleaseError(f"release {tag} exists already: this map is published (fetch it, or build a new one)")
    if target is None:
        target = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    if not target or not gh.commit_exists(target):
        raise ReleaseError(f"commit {target[:12] or '?'} isn't on GitHub: push e39-rpi-maps first "
                           "(the release's tag points at it)")

    log("Checksumming...")
    entries = {"database": _file_entry(db_path)}
    tile_count = 0
    for c, path in pairs:
        key = "routing" if c.kind == "routing" else f"tiles-{(tile_count := tile_count + 1):02d}"
        entries[key] = _file_entry(path, c.sha256)
    notes = "\n".join([
        f"Map built {meta['built_at']} by the e39-rpi map toolkit ({meta.get('toolkit_version', '')}).",
        "",
        f"- Region: {meta.get('region_label') or meta.get('region', '')}"
        + (f" + {meta['radius_km']} km" if meta.get("radius_km") else ""),
        f"- Time zone: {meta.get('timezone') or 'not recorded'}",
        f"- POIs: {meta['poi_count']} ({meta.get('categories', '')})",
        f"- Routing tiles: " + (f"Valhalla {routing['valhalla_version']}, "
                                f"{int(routing['routing_size']) / 1e6:.0f} MB" if routing else "none"),
        f"- Offline map tiles (OSM style): " + (f"zooms {meta.get('tiles_zooms', '')}, {meta.get('tiles_count', '?')} tiles "
                                                 f"in {tile_count} file(s)" if tile_count else "none"),
        f"- OSM data: {meta.get('extracts', '')}",
        "",
        "Map data (c) OpenStreetMap contributors, ODbL 1.0.",
    ])
    log(f"Creating release {tag} on {gh.repo}...")
    release = gh.create_release(tag, target, title_for(meta), notes)
    try:
        for f in files:
            log(f"Uploading {f.name} ({f.stat().st_size / 1e6:.0f} MB)...")
            started = time.monotonic()
            gh.upload(release, f, progress)
            log(f"  uploaded in {time.monotonic() - started:.0f} s")
    except BaseException:
        log("Upload failed: deleting the incomplete release")
        try:
            gh.delete_release(release["id"])
        except ReleaseError:
            pass
        raise

    manifest = {
        "repo": gh.repo,
        "tag": tag,
        "built_at": meta["built_at"],
        "region": meta.get("region", ""),
        "region_label": meta.get("region_label", ""),
        "region_slug": meta.get("region_slug", ""),
        "timezone": meta.get("timezone", ""),
        "poi_count": int(meta["poi_count"]),
        "valhalla_version": meta.get("valhalla_version"),
        "files": entries,
    }
    text = json.dumps(manifest, indent=2) + "\n"
    # The release describes itself too: fetching any release (not just the
    # pinned one) reads this asset.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asset = Path(tmp) / MANIFEST_NAME
        asset.write_text(text)
        try:
            gh.upload(release, asset)
        except BaseException:
            gh.delete_release(release["id"])
            raise
    if pin is None:
        pin = should_pin(meta, repo_root)
    if pin:
        manifest_path(repo_root).write_text(text)
        log(f"Published {tag}. Wrote {MANIFEST_NAME}: commit and push it so builds use this map.")
    else:
        log(f"Published {tag}. Not pinned: {MANIFEST_NAME} keeps "
            f"{(read_manifest(repo_root) or {}).get('tag')} (another region) for image builds.")
    return manifest


def matches(manifest, dest_dir):
    """Is the manifest's map already in dest_dir (by size and SHA-256)?"""
    dest_dir = Path(dest_dir)
    for entry in manifest["files"].values():
        path = dest_dir / entry["name"]
        if not path.exists() or path.stat().st_size != entry["size"] \
                or schema.sha256_file(path) != entry["sha256"]:
            return False
    return True


def fetch(manifest, dest_dir, gh=None, log=print, progress=None, force=False):
    """Download the manifest's map into dest_dir, checked. Refuses to replace
    a different local map there unless force."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    db_entry = manifest["files"]["database"]
    db = dest_dir / db_entry["name"]
    if matches(manifest, dest_dir):
        log(f"{dest_dir} has the published map {manifest['tag']} already")
        return db
    if db.exists() and not force:
        try:
            local = schema.check(db, quick_check=False)
            what = f"a different map (built {local['built_at']})"
        except schema.InvalidMapDatabase:
            what = "a file that isn't a map database"
        raise ReleaseError(f"{db} is {what}, not {manifest['tag']}: "
                           "use --force (or the toolkit's Fetch) to replace it")

    gh = gh or GitHub(manifest.get("repo", DEFAULT_REPO))
    release = gh.release_by_tag(manifest["tag"])
    if release is None:
        raise ReleaseError(f"release {manifest['tag']} isn't on {gh.repo} (deleted?)")
    assets = {a["name"]: a for a in release.get("assets", [])}
    downloaded = []
    try:
        for entry in manifest["files"].values():
            asset = assets.get(entry["name"])
            if asset is None:
                raise ReleaseError(f"release {manifest['tag']} has no {entry['name']}")
            part = dest_dir / f".{entry['name']}.download"
            log(f"Downloading {entry['name']} ({entry['size'] / 1e6:.0f} MB)...")
            gh.download(asset, part, progress)
            if part.stat().st_size != entry["size"] or schema.sha256_file(part) != entry["sha256"]:
                raise ReleaseError(f"{entry['name']}: downloaded file doesn't match the manifest (SHA-256)")
            downloaded.append((part, dest_dir / entry["name"]))
        if "ATTRIBUTION.txt" in assets and not (dest_dir / "ATTRIBUTION.txt").exists():
            gh.download(assets["ATTRIBUTION.txt"], dest_dir / "ATTRIBUTION.txt")
    except BaseException:
        for part, _ in downloaded:
            part.unlink(missing_ok=True)
        for entry in manifest["files"].values():
            (dest_dir / f".{entry['name']}.download").unlink(missing_ok=True)
        raise
    # Companion files first, then the database that names them.
    for part, final in sorted(downloaded, key=lambda pf: pf[1] == db):
        os.replace(part, final)
    meta = schema.check(db)
    schema.check_companions(meta, dest_dir, verify_hash=False)
    log(f"Fetched {manifest['tag']} into {dest_dir}")
    return db


def pin_release(tag, repo_root, gh=None, log=print):
    """Pin an already published release: write its manifest as
    map-release.json (e.g. switching image builds back to another region's
    map). Returns the manifest."""
    manifest = release_manifest(tag, gh)
    manifest_path(repo_root).write_text(json.dumps(manifest, indent=2) + "\n")
    log(f"Pinned {tag}. Wrote {MANIFEST_NAME}: commit and push it so builds use this map.")
    return manifest


def release_manifest(tag, gh=None):
    """The manifest a release carries (its map-release.json asset)."""
    import tempfile

    gh = gh or GitHub()
    release = gh.release_by_tag(tag)
    if release is None:
        raise ReleaseError(f"no release {tag} on {gh.repo}")
    asset = next((a for a in release.get("assets", []) if a["name"] == MANIFEST_NAME), None)
    if asset is None:
        raise ReleaseError(f"release {tag} has no {MANIFEST_NAME} (not published by the map toolkit?)")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / MANIFEST_NAME
        gh.download(asset, path)
        return json.loads(path.read_text())


def fetch_release(tag, dest_dir, gh=None, log=print, progress=None, force=False):
    """Download the published map `tag` into dest_dir; returns the database path."""
    gh = gh or GitHub()
    return fetch(release_manifest(tag, gh), dest_dir, gh=gh, log=log, progress=progress, force=force)
