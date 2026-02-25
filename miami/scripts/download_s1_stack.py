#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import netrc
import shutil
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import asf_search as asf


@dataclass
class DownloadItem:
    scene_name: str
    url: str
    expected_bytes: int | None
    filename: str
    dest_path: Path
    status: str
    existing_bytes: int


def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, path_value: str) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def read_earthdata_creds() -> tuple[str, str]:
    machine_candidates = [
        "urs.earthdata.nasa.gov",
        "urs.earthdata.nasa.gov:443",
        "earthdata.nasa.gov",
    ]
    nrc = netrc.netrc()

    for machine in machine_candidates:
        auth = nrc.authenticators(machine)
        if auth:
            login, account, password = auth
            username = login or account
            if username and password:
                return username, password

    raise RuntimeError(
        "No Earthdata credentials found in ~/.netrc. "
        "Add: machine urs.earthdata.nasa.gov login <username> password <password>"
    )


def parse_csv_items(scene_csv: Path, slc_dir: Path) -> list[DownloadItem]:
    items: list[DownloadItem] = []

    with scene_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scene_name = row.get("sceneName", "").strip()
            url = row.get("url", "").strip()
            bytes_str = row.get("bytes", "").strip()
            expected_bytes = int(bytes_str) if bytes_str else None

            if not url:
                raise ValueError(f"Missing URL for scene: {scene_name}")

            filename = Path(urlparse(url).path).name
            if not filename:
                filename = f"{scene_name}.zip"

            dest_path = slc_dir / filename

            if dest_path.exists():
                existing_bytes = dest_path.stat().st_size
                if expected_bytes is not None and existing_bytes == expected_bytes:
                    status = "complete"
                else:
                    status = "partial"
            else:
                existing_bytes = 0
                status = "missing"

            items.append(
                DownloadItem(
                    scene_name=scene_name,
                    url=url,
                    expected_bytes=expected_bytes,
                    filename=filename,
                    dest_path=dest_path,
                    status=status,
                    existing_bytes=existing_bytes,
                )
            )

    return items


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"downloads": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def update_manifest(manifest: dict, item: DownloadItem, status: str, message: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    manifest["downloads"][item.scene_name] = {
        "scene_name": item.scene_name,
        "filename": item.filename,
        "url": item.url,
        "status": status,
        "message": message,
        "updated_utc": now,
        "expected_bytes": item.expected_bytes,
        "existing_bytes": item.dest_path.stat().st_size if item.dest_path.exists() else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download selected Sentinel-1 SLC scenes listed in stack search outputs. "
            "Default mode is dry-run summary."
        )
    )
    parser.add_argument(
        "--config",
        default="miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml",
        help="Path to stack TOML config.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Execute downloads. Without this flag, only print a dry-run plan.",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=0,
        help="Optional cap for number of pending scenes to download (0 = no cap).",
    )
    parser.add_argument(
        "--reserve-gb",
        type=float,
        default=20.0,
        help="Required free-space reserve in GB after planned downloads.",
    )
    parser.add_argument(
        "--overhead-fraction",
        type=float,
        default=0.05,
        help="Extra space multiplier on pending download size for safety checks.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore space guard checks and proceed.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = resolve_path(repo_root, args.config)
    cfg = read_toml(config_path)
    out_cfg = cfg["outputs"]
    storage_cfg = cfg["storage"]

    out_root = resolve_path(repo_root, out_cfg["root"])
    scene_csv = out_root / out_cfg["metadata_csv"]
    slc_dir = resolve_path(repo_root, storage_cfg["slc_dir"])
    manifest_path = resolve_path(repo_root, storage_cfg["download_manifest"])
    slc_dir.mkdir(parents=True, exist_ok=True)

    if not scene_csv.exists():
        print(f"Missing scene metadata CSV: {scene_csv}", file=sys.stderr)
        print("Run stack search first.", file=sys.stderr)
        return 2

    items = parse_csv_items(scene_csv, slc_dir)
    pending = [i for i in items if i.status in {"missing", "partial"}]
    complete = [i for i in items if i.status == "complete"]

    if args.max_scenes > 0:
        pending = pending[: args.max_scenes]

    pending_known_bytes = sum(i.expected_bytes or 0 for i in pending)
    unknown_size_count = sum(1 for i in pending if i.expected_bytes is None)

    usage = shutil.disk_usage(slc_dir)
    free_bytes = usage.free
    reserve_bytes = int(args.reserve_gb * 1e9)
    guarded_required = int(pending_known_bytes * (1.0 + args.overhead_fraction)) + reserve_bytes

    print(f"Config: {config_path}")
    print(f"Scene CSV: {scene_csv}")
    print(f"SLC dir: {slc_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Total listed scenes: {len(items)}")
    print(f"Already complete: {len(complete)}")
    print(f"Pending: {len(pending)}")
    print(f"Pending known size: {pending_known_bytes / 1e9:.2f} GB (decimal)")
    print(f"Pending unknown-size scenes: {unknown_size_count}")
    print(f"Free space at target FS: {free_bytes / 1e9:.2f} GB (decimal)")
    print(
        "Guarded required space "
        f"(pending * (1+{args.overhead_fraction:.2f}) + reserve {args.reserve_gb:.1f} GB): "
        f"{guarded_required / 1e9:.2f} GB"
    )

    if not args.download:
        print("\nDry-run only. Re-run with --download to execute.")
        return 0

    if not args.force and pending_known_bytes > 0 and free_bytes < guarded_required:
        print(
            "\nAborting: free space below guarded requirement. "
            "Use --force only if you understand the risk.",
            file=sys.stderr,
        )
        return 3

    username, password = read_earthdata_creds()
    session = asf.ASFSession().auth_with_creds(username, password)
    manifest = load_manifest(manifest_path)

    for idx, item in enumerate(pending, start=1):
        print(
            f"[{idx}/{len(pending)}] {item.scene_name} "
            f"({item.expected_bytes / 1e9:.2f} GB)" if item.expected_bytes else f"[{idx}/{len(pending)}] {item.scene_name}"
        )

        if item.dest_path.exists():
            current_size = item.dest_path.stat().st_size
            if item.expected_bytes is not None and current_size != item.expected_bytes:
                # Clean partial files because asf.download_url skips existing filenames.
                item.dest_path.unlink()

        try:
            asf.download_url(
                url=item.url,
                path=str(slc_dir),
                filename=item.filename,
                session=session,
            )
            if item.expected_bytes is not None:
                got = item.dest_path.stat().st_size if item.dest_path.exists() else 0
                if got != item.expected_bytes:
                    raise RuntimeError(
                        f"Size mismatch for {item.filename}: expected {item.expected_bytes}, got {got}"
                    )

            update_manifest(manifest, item, status="downloaded")
            save_manifest(manifest_path, manifest)
        except Exception as e:  # noqa: BLE001
            update_manifest(manifest, item, status="failed", message=str(e))
            save_manifest(manifest_path, manifest)
            print(f"Download failed for {item.scene_name}: {e}", file=sys.stderr)
            return 4

    print("\nDownload stage completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
