import os
import runpy
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

module = runpy.run_path(str(Path(__file__).parents[1] / "cleanup-images.py"))
candidates = module["candidates"]
remove_image = module["remove_image"]
created_at = module["created_at"]
REPO = "ghcr.io/mirror29/inalpha-data"
CUTOFF = datetime(2026, 10, 13, tzinfo=timezone.utc)


def image(number, repo=REPO, tags=None):
    return {
        "Id": "sha256:" + str(number),
        "Created": f"2026-10-{number:02d}T00:00:00Z",
        "RepoTags": tags if tags is not None else [f"{repo}:v{number}"],
        "RepoDigests": [],
    }


class CleanupImagesTests(unittest.TestCase):
    def test_normalizes_docker_fractional_timestamps_for_older_python(self):
        self.assertEqual(
            created_at("2026-10-13T00:00:00.123456789Z"),
            datetime(2026, 10, 13, microsecond=123456, tzinfo=timezone.utc),
        )
        self.assertEqual(
            created_at("2026-10-13T00:00:00.1Z"),
            datetime(2026, 10, 13, microsecond=100000, tzinfo=timezone.utc),
        )

    def test_preserves_two_latest_and_container_and_rollback_images(self):
        images = [image(n) for n in range(1, 8)]
        selected = candidates(images, {REPO}, {"sha256:1", "sha256:2"}, 2, CUTOFF)
        self.assertEqual(
            [i["Id"] for i in selected], ["sha256:3", "sha256:4", "sha256:5"]
        )

    def test_keeps_at_least_two_for_each_repository(self):
        second = "ghcr.io/mirror29/inalpha-paper"
        images = [image(n) for n in (1, 2, 3)] + [image(n, second) for n in (4, 5, 6)]
        selected = candidates(images, {REPO, second}, set(), 2, CUTOFF)
        self.assertEqual([i["Id"] for i in selected], ["sha256:1", "sha256:4"])

    def test_multiple_tags_count_as_one_version(self):
        images = [image(1), image(2), image(3, tags=[REPO + ":a", REPO + ":b"])]
        self.assertEqual(candidates(images, {REPO}, set(), 2, CUTOFF), [images[0]])

    def test_preserves_other_repositories_and_shared_tags(self):
        foreign = image(1, "redis")
        shared = image(2, tags=[REPO + ":old", "other/data:backup"])
        images = [foreign, shared, image(3), image(4)]
        self.assertEqual(candidates(images, {REPO}, set(), 2, CUTOFF), [])

    def test_preserves_recent_builds_and_unknown_dangling_images(self):
        images = [image(1, tags=[]), image(13), image(14), image(15)]
        self.assertEqual(candidates(images, {REPO}, set(), 2, CUTOFF), [])

    def test_recognizes_untagged_images_only_with_explicit_digest_repository(self):
        old = image(1, tags=[])
        old["RepoDigests"] = [REPO + "@sha256:old"]
        self.assertEqual(
            candidates([old, image(2), image(3)], {REPO}, set(), 2, CUTOFF), [old]
        )

    def test_rechecks_containers_before_deleting_each_tag(self):
        old = image(1, tags=[REPO + ":a", REPO + ":b"])
        with (
            patch.dict(
                remove_image.__globals__,
                {
                    "container_images": unittest.mock.Mock(
                        side_effect=[set(), {old["Id"]}]
                    ),
                    "docker": unittest.mock.Mock(return_value=old["Id"]),
                },
            ),
            patch("subprocess.run") as run,
        ):
            remove_image(old, set())
        run.assert_called_once_with(
            ["docker", "image", "rm", "--no-prune", REPO + ":a"], check=True
        )

    def test_refuses_changed_image_reference(self):
        with (
            patch.dict(
                remove_image.__globals__,
                {
                    "container_images": lambda: set(),
                    "docker": lambda *args: "sha256:replacement",
                },
            ),
            patch("subprocess.run") as run,
            self.assertRaises(RuntimeError),
        ):
            remove_image(image(1), set())
        run.assert_not_called()

    def test_delete_failure_is_not_ignored(self):
        with (
            patch.dict(
                remove_image.__globals__,
                {
                    "container_images": lambda: set(),
                    "docker": lambda *args: "sha256:1",
                },
            ),
            patch("subprocess.run", side_effect=RuntimeError("in use")),
            self.assertRaises(RuntimeError),
        ):
            remove_image(image(1), set())


class DeploymentTests(unittest.TestCase):
    def check_deployment(self, up_result):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("scripts", "infra", "bin"):
                (root / name).mkdir()
            shutil.copy(
                Path(__file__).parents[1] / "deploy.sh", root / "scripts/deploy.sh"
            )
            (root / "infra/.env.prod").write_text("CLOUDFLARE_TUNNEL_TOKEN=test\n")
            fake_docker = """#!/usr/bin/env bash
echo "$*" >> "$TEST_CALLS"
case "$*" in
  "ps -aq") echo container1 ;;
  "inspect "*) echo sha256:previous ;;
  *"config --images") echo ghcr.io/mirror29/inalpha-data:v3 ;;
  *" up "*) exit "$TEST_UP_RESULT" ;;
esac
"""
            fake_python = """#!/usr/bin/env bash
echo "cleanup $*" >> "$TEST_CALLS"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--protect-file" ]; then cat "$2" >> "$TEST_CALLS"; break; fi
  shift
done
"""
            for name, content in {
                "docker": fake_docker,
                "python3": fake_python,
                "flock": "#!/bin/sh\nexit 0\n",
            }.items():
                executable = root / "bin" / name
                executable.write_text(content)
                executable.chmod(0o755)
            calls = root / "calls"
            env = dict(
                os.environ,
                PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                TEST_CALLS=str(calls),
                TEST_UP_RESULT=str(up_result),
            )
            result = subprocess.run(
                ["bash", str(root / "scripts/deploy.sh"), "--no-pull"],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            return result, calls.read_text()

    def test_failed_deployment_never_cleans_images(self):
        result, calls = self.check_deployment(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("cleanup ", calls)

    def test_successful_deployment_waits_then_cleans_with_rollback_protection(self):
        result, calls = self.check_deployment(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(
            calls.index("--wait --wait-timeout 300"), calls.index("cleanup ")
        )
        self.assertIn("--repository ghcr.io/mirror29/inalpha-data", calls)
        self.assertTrue(calls.endswith("sha256:previous\n"))


if __name__ == "__main__":
    unittest.main()
