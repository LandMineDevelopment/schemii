import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "scripts/inspect-release-artifacts.py"
MANIFEST = ROOT / "scripts/release-manifest.py"


def tar_bytes(files, mode="w:gz"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as archive:
        for name, payload in files.items():
            value = payload.encode() if isinstance(payload, str) else payload
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return output.getvalue()


def write_tar(path, files, mode="w:gz"):
    path.write_bytes(tar_bytes(files, mode))


def write_image(path, files):
    write_tar(path, {"layer/layer.tar": tar_bytes(files, "w:")})


class ReleaseArtifactTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source.tar.gz"
        packages = root / "packages.tar.gz"
        application = root / "application.tar.gz"
        metadata = root / "metadata.tar.gz"
        opencode = root / "opencode.tar.gz"
        write_tar(source, {
            "schemii-0.2.0/README.md": "public source",
            "schemii-0.2.0/examples/postgres/001_bookstore.sql": "select 1;",
        })
        wheel_bytes = io.BytesIO()
        with zipfile.ZipFile(wheel_bytes, "w") as wheel:
            wheel.writestr("schemii/build_revision.txt", "a" * 40)
        sdist = tar_bytes({"schemii-0.2.0/src/schemii/build_revision.txt": "a" * 40})
        write_tar(packages, {"schemii-0.2.0.whl": wheel_bytes.getvalue(), "schemii-0.2.0.tar.gz": sdist})
        write_image(application, {"opt/venv/lib/python3.12/site-packages/schemii/build_revision.txt": "a" * 40})
        write_image(metadata, {"opt/schemii-release-version": "0.2.0", "opt/schemii-release-revision": "a" * 40})
        write_image(opencode, {"opt/opencode/node_modules/example/index.js": "export {};"})
        return source, packages, application, metadata, opencode

    def inspect(self, fixture):
        source, packages, application, metadata, opencode = fixture
        return subprocess.run([
            "python3", str(INSPECTOR), "--source", str(source), "--packages", str(packages),
            "--image", f"application={application}", "--image", f"metadata={metadata}",
            "--image", f"opencode={opencode}",
        ], cwd=ROOT, capture_output=True, text=True)

    def test_artifact_inspection_accepts_only_the_approved_seed_and_clean_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.inspect(self.fixture(Path(temporary)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("only approved synthetic database data", result.stdout)

    def test_artifact_inspection_rejects_private_profiles_and_image_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.fixture(root)
            write_tar(fixture[0], {
                "schemii-0.2.0/examples/postgres/001_bookstore.sql": "select 1;",
                "schemii-0.2.0/postgres_profiles.json": '{"profiles": []}',
            })
            result = self.inspect(fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("forbidden runtime or database data", result.stderr)

            fixture = self.fixture(root)
            write_image(fixture[2], {
                "opt/venv/lib/python3.12/site-packages/schemii/build_revision.txt": "a" * 40,
                "data/schemas/customer.json": "{}",
            })
            result = self.inspect(fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("persisted user data", result.stderr)

    def test_manifest_binds_hashes_and_exact_image_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            release.mkdir()
            prefix = f"schemii-0.2.0-{'a' * 40}"
            for suffix in (
                "source.tar.gz", "python-packages.tar.gz", "application-linux-amd64.tar.gz",
                "metadata-linux-amd64.tar.gz", "opencode-linux-amd64.tar.gz",
            ):
                (release / f"{prefix}-{suffix}").write_bytes(suffix.encode())
            docker = root / "docker"
            docker.write_text(
                "#!/bin/sh\ncase \"$3\" in schemii:*) value=1 ;; schemii-metadata-*) value=2 ;; schemii-opencode:*) value=3 ;; esac\nprintf '[{\"Id\":\"sha256:%064d\",\"Os\":\"linux\",\"Architecture\":\"amd64\",\"Config\":{\"Labels\":{\"org.opencontainers.image.version\":\"0.2.0\",\"org.opencontainers.image.revision\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}}]' \"$value\"\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            create = subprocess.run([
                "python3", str(MANIFEST), "create", "--release-dir", str(release),
                "--version", "0.2.0", "--revision", "a" * 40, "--platform", "linux/amd64",
                "--application-image", f"schemii:0.2.0-{'a' * 40}",
                "--metadata-image", f"schemii-metadata-postgres:0.2.0-{'a' * 40}",
                "--opencode-image", f"schemii-opencode:0.2.0-{'a' * 40}",
            ], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(create.returncode, 0, create.stderr)
            manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["revision"], "a" * 40)
            self.assertEqual(set(manifest["images"]), {"application", "metadata", "opencode"})
            checksum_lines = []
            import hashlib
            for path in sorted(release.iterdir()):
                checksum_lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
            (release / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
            verified = subprocess.run([
                "python3", str(MANIFEST), "verify", "--release-dir", str(release),
                "--version", "0.2.0", "--revision", "a" * 40,
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            (release / f"{prefix}-source.tar.gz").write_bytes(b"tampered")
            rejected = subprocess.run([
                "python3", str(MANIFEST), "verify", "--release-dir", str(release),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("hashes do not match", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
