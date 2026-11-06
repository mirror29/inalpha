#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def container_images():
    containers = docker("ps", "-aq").split()
    if not containers:
        return set()
    return set(docker("inspect", "--format", "{{.Image}}", *containers).split())


def repositories(image):
    tags = image.get("RepoTags") or []
    digests = image.get("RepoDigests") or []
    return {tag.rsplit(":", 1)[0] for tag in tags} | {
        digest.split("@", 1)[0] for digest in digests
    }


def created_at(value):
    normalized = re.sub(
        r"\.(\d+)", lambda match: "." + match[1][:6].ljust(6, "0"), value
    )
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def candidates(images, allowed, protected, keep, cutoff):
    grouped = {repo: [] for repo in allowed}
    for image in images:
        for repo in repositories(image) & allowed:
            grouped[repo].append(image)
    retained = set(protected)
    for group in grouped.values():
        ordered = sorted(group, key=lambda image: image["Created"], reverse=True)
        retained.update(image["Id"] for image in ordered[:keep])
    return [
        image
        for image in images
        if repositories(image)
        and repositories(image) <= allowed
        and image["Id"] not in retained
        and created_at(image["Created"]) < cutoff
    ]


def remove_image(image, protected):
    image_id = image["Id"]
    refs = image.get("RepoTags") or [image_id]
    for ref in refs:
        if image_id in protected | container_images():
            print("SKIP in-use:", image_id)
            return
        actual = docker("image", "inspect", "--format", "{{.Id}}", ref)
        if actual != image_id:
            raise RuntimeError("Image reference changed; stopping cleanup: " + ref)
        subprocess.run(["docker", "image", "rm", "--no-prune", ref], check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Preview or remove old Inalpha images."
    )
    parser.add_argument("--repository", action="append", required=True)
    parser.add_argument("--protect-file", type=Path)
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--min-age-hours", type=int, default=24)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.keep < 2 or args.min_age_hours < 24:
        parser.error("Keep at least two versions and a minimum age of 24 hours.")
    allowed = set(args.repository)
    services = {"data", "paper", "research", "factor", "evolver", "mastra", "dashboard"}
    if any(
        repo.rsplit("/", 1)[-1] not in {"inalpha-" + s for s in services}
        for repo in allowed
    ):
        parser.error("Only explicit Inalpha application repositories are supported.")
    protected = container_images()
    if args.protect_file:
        protected.update(args.protect_file.read_text().split())
    ids = sorted(set(docker("image", "ls", "-aq", "--no-trunc").split()))
    images = json.loads(docker("image", "inspect", *ids)) if ids else []
    selected = candidates(
        images,
        allowed,
        protected,
        args.keep,
        datetime.now(timezone.utc) - timedelta(hours=args.min_age_hours),
    )
    print("APPLY" if args.apply else "DRY RUN", "candidate images:", len(selected))
    for image in selected:
        print(image["Id"], ", ".join(image.get("RepoTags") or []))
        if args.apply:
            remove_image(image, protected)


if __name__ == "__main__":
    main()
