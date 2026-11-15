#!/usr/bin/env python3
"""Host controls against pinned, ZIP-verified SDK Android socket control source.

Uses Go overlays in a fresh private output directory; never edits the SDK/cache.
Only the Android build constraint is changed for host execution. The optional
capability's atomic flag is set directly because its exported setter checks GOOS.
This is source-extracted Linux execution, NOT Android/JNI/runtime acceptance.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[2]
FIXTURES = MODULE / "testdata/sdk-control"
SDK = "tailscale.com@v1.104.7"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_guards(sdk):
    # Inspection controls of the real construction chain. In particular, the
    # fallback API's nil-monitor path and controlhttp's nil-dialer path do NOT
    # bind sockets. Do not claim they are safe; verify this consumer avoids them.
    checks = {
        "net/dnsfallback/dnsfallback.go": [
            "var dialer netns.Dialer = new(net.Dialer)",
            "if netMon != nil {\n\t\tdialer = netns.NewDialer(logf, netMon)",
            'net.JoinHostPort(serverIP.String(), "443")',
            '"https://"+serverName+"/bootstrap-dns?q="+url.QueryEscape(queryName)',
            "tr.TLSClientConfig = tlsdial.Config(ht, tr.TLSClientConfig)",
        ],
        "control/controlclient/direct.go": [
            "netMon := opts.Dialer.NetMon()\n\tif netMon == nil {",
            'return nil, errors.New("controlclient.New: Dialer has nil NetMon")',
            "LookupIPFallback: dnsfallback.MakeLookupFunc(opts.Logf, netMon)",
            "netMon:              netMon,",
            "Dialer:        c.dialer,",
            "DNSCache:      c.dnsCache,",
            "NetMon:        c.netMon,",
        ],
        "control/ts2021/client.go": [
            "Dialer:          nc.opts.Dialer.SystemDial,",
            "DNSCache:        nc.opts.DNSCache,",
            "NetMon:          nc.opts.NetMon,",
        ],
        "control/controlhttp/client.go": [
            "LookupIPFallback: dnsfallback.MakeLookupFunc(a.logf, a.NetMon)",
            "if a.Dialer != nil {\n\t\tdialer = a.Dialer\n\t} else {\n\t\tdialer = stdDialer.DialContext",
        ],
        "tsnet/tsnet.go": [
            "s.netMon, err = netmon.New(sys.Bus.Get(), tsLogf)",
            "NetMon:        s.netMon,",
            "Dialer:        s.dialer,",
            "logpolicy.NewLogtailTransport(logtail.DefaultHost, s.netMon, health, tsLogf)",
        ],
        "wgengine/userspace.go": ["conf.Dialer.SetNetMon(e.netMon)"],
        "logpolicy/logpolicy.go": [
            "if netMon == nil {\n\t\tnetMon = netmon.NewStatic()",
            "if opts.NetMon == nil {\n\t\topts.NetMon = netmon.NewStatic()",
            "tr.DialContext = MakeDialFunc(opts.NetMon, opts.Logf)",
            "nd := netns.FromDialer(logf, netMon, &net.Dialer{",
            "LookupIPFallback: dnsfallback.MakeLookupFunc(logf, netMon)",
        ],
        "net/tsdial/tsdial.go": [
            'return nil, errors.New("SystemDial requires a netmon.Monitor; call SetNetMon first")',
            "d.netnsDialer = netns.NewDialer(d.logf, d.netMon)",
        ],
        "derp/derphttp/derphttp_client.go": [
            'if netMon == nil {\n\t\tpanic("nil netMon")',
            "dialer := netns.NewDialer(c.logf, c.netMon)",
        ],
        "wgengine/magicsock/magicsock.go": [
            "case opts.NetMon == nil:",
            "netns.Listener(c.logf, c.netMon)",
        ],
        "ipn/ipnlocal/local.go": ["netns.SetDisableAndroidBindToActiveNetwork"],
        "net/netns/netns.go": [
            'runtime.GOOS == "android" && disableAndroidBindToActiveNetwork.Swap(v)',
        ],
    }
    for path, snippets in checks.items():
        body = (sdk / path).read_text()
        for snippet in snippets:
            if " ".join(snippet.split()) not in " ".join(body.split()):
                raise ValueError(
                    f"SDK construction boundary changed: {path}: {snippet!r}"
                )
    callers = sorted(
        str(p.relative_to(sdk))
        for p in sdk.rglob("*.go")
        if not p.name.endswith("_test.go")
        and "dnsfallback.MakeLookupFunc(" in p.read_text()
    )
    expected = [
        "control/controlclient/direct.go",
        "control/controlhttp/client.go",
        "logpolicy/logpolicy.go",
    ]
    if callers != expected:
        raise ValueError(f"Unreviewed SDK fallback caller: {callers}")
    return checks, callers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--go-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--registration-ref",
        help="Counterexample: extract install hooks from this Git commit",
    )
    args = parser.parse_args()
    if "\ttailscale.com v1.104.7\n" not in (MODULE / "go.mod").read_text():
        raise ValueError("SDK pin changed; review these source-extracted controls")
    go = args.go.resolve(strict=True)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    env = os.environ.copy()
    env.update(
        GOTOOLCHAIN="local",
        GOPROXY="off",
        GOCACHE=str(args.go_cache.resolve()),
        GOMAXPROCS="3",
    )
    for key in (
        "GOOS",
        "GOARCH",
        "CGO_CFLAGS",
        "CGO_LDFLAGS",
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        env.pop(key, None)
    env["GOOS"], env["GOARCH"] = "linux", "amd64"
    if (
        subprocess.check_output([go, "version"], env=env, text=True).strip()
        != "go version go1.27.4 linux/amd64"
    ):
        raise ValueError("Expected canonical Go1.27.4")
    modcache = Path(
        subprocess.check_output([go, "env", "GOMODCACHE"], env=env, text=True).strip()
    )
    cached_sdk = modcache / SDK
    # Verify every SDK file used in the construction trace against its pinned
    # module ZIP; production module files remain untouched.
    guards, callers = source_guards(cached_sdk)
    inspected = sorted(
        set(guards)
        | {
            "net/netns/netns_android.go",
            "net/netns/netns_linux.go",
            "net/dnscache/dnscache.go",
            "net/tlsdial/tlsdial.go",
        }
    )
    sdk_hashes = {}
    module_zip = modcache / "cache/download/tailscale.com/@v/v1.104.7.zip"
    with zipfile.ZipFile(module_zip) as archive:
        for path in inspected:
            raw = (cached_sdk / path).read_bytes()
            if raw != archive.read(f"{SDK}/{path}"):
                raise ValueError(f"SDK differs from pinned ZIP: {path}")
            sdk_hashes[path] = sha(raw)
        # Go refuses overlays beneath GOMODCACHE. Use an exact private ZIP
        # extraction for this host-only test; no SDK or production modfile edits.
        archive.extractall(out / "sdk-source")
    sdk = out / "sdk-source" / SDK
    modfile = out / "host-controls.mod"
    modfile.write_bytes(
        (MODULE / "go.mod").read_bytes()
        + f"\nreplace tailscale.com => {sdk}\n".encode()
    )
    modfile.with_suffix(".sum").write_bytes((MODULE / "go.sum").read_bytes())
    inputs = [
        p for p in MODULE.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    ]
    inventory = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in inputs}
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    registration = (MODULE / "apk_android.go").read_bytes()
    if args.registration_ref:
        registration = subprocess.check_output(
            [
                "git",
                "show",
                f"{args.registration_ref}:dev/android/tailnet-proxy/apk_android.go",
            ],
            cwd=ROOT,
        )
    install = (
        registration.decode()
        .split("func (p *apkPlatform) install() error {", 1)[1]
        .split("func (p *apkPlatform) configure", 1)[0]
    )
    hooks = re.findall(
        r"netns\.(SetAndroidProtectFunc|SetAndroidBindToNetworkFunc|SetEnabled)\((p\.bindSocket|nil|true)\)",
        install,
    )
    if len(hooks) != 3 or len({name for name, _ in hooks}) != 3:
        raise ValueError("Unexpected adapter hook registration; inspect manually")
    packages = [
        "net/netns",
        "net/dnsfallback",
        "control/controlclient",
        "control/controlhttp",
    ]
    # Compile the production packages with only our focused controls. Upstream
    # test suites need unrelated, unretained test dependencies; do not fetch any.
    replacements = {
        str(p): "" for package in packages for p in (sdk / package).glob("*_test.go")
    }
    serial = 0

    def overlay(virtual, body):
        nonlocal serial
        physical = out / f"overlay-{serial}.go"
        serial += 1
        physical.write_bytes(body)
        replacements[str(sdk / virtual)] = str(physical)

    android = (sdk / "net/netns/netns_android.go").read_bytes()
    overlay(
        "net/netns/netns_linux.go",
        android.replace(b"//go:build android", b"//go:build linux", 1),
    )
    # Android excludes SOCKS proxy wrapping. Match that package file selection
    # on the host without modifying any compiled SDK control/dialer body.
    overlay("net/netns/socks.go", b"//go:build schemii_unused\n\npackage netns\n")
    overlay("net/netns/netns_linux_test.go", (FIXTURES / "netns_test.go").read_bytes())
    overlay("net/netns/schemii_fixture.go", (FIXTURES / "fixture.go").read_bytes())
    epoch = (MODULE / "socket_epoch_linux.go").read_bytes()
    overlay(
        "net/netns/schemii_epoch.go",
        epoch.replace(b"package main", b"package netns", 1),
    )
    extracted = (
        "package netns\nfunc schemiiInstallSource(p *schemiiPlatform) {\n"
        + "\n".join(f"{name}({value})" for name, value in hooks)
        + "\n}\n"
    )
    overlay("net/netns/schemii_install.go", extracted.encode())
    overlay(
        "net/dnsfallback/schemii_control_test.go",
        (FIXTURES / "bootstrap_test.go").read_bytes(),
    )
    overlay(
        "control/controlclient/schemii_control_test.go",
        (FIXTURES / "controlclient_test.go").read_bytes(),
    )
    overlay(
        "control/controlhttp/schemii_control_test.go",
        (FIXTURES / "controlhttp_test.go").read_bytes(),
    )
    overlay_file = out / "overlay.json"
    overlay_file.write_text(json.dumps({"Replace": replacements}, indent=2) + "\n")
    command = [
        str(go),
        "test",
        "-mod=readonly",
        f"-modfile={modfile}",
        "-p=3",
        f"-overlay={overlay_file}",
        "-count=1",
        "-timeout=45s",
        "-json",
        "-run=^TestSchemii",
        "tailscale.com/net/netns",
        "tailscale.com/net/dnsfallback",
        "tailscale.com/control/controlclient",
        "tailscale.com/control/controlhttp",
    ]
    start = time.time()
    with (out / "test.log").open("x") as log:
        result = subprocess.run(
            command, cwd=MODULE, env=env, stdout=log, stderr=subprocess.STDOUT
        )
    record = {
        "schema": 1,
        "kind": "source-extracted-host-sdk-controls",
        "head": head,
        "command": command,
        "cwd": str(MODULE),
        "exit": result.returncode,
        "elapsed_seconds": time.time() - start,
        "go_compiler_sha256": sha(go.read_bytes()),
        "source_files": inventory,
        "registration_ref": args.registration_ref,
        "registration_source_sha256": sha(registration),
        "extracted_hooks": hooks,
        "sdk_version": SDK,
        "sdk_zip_verified_files": sdk_hashes,
        "sdk_zip_sha256": sha(module_zip.read_bytes()),
        "test_only_modfile_sha256": sha(modfile.read_bytes()),
        "inspected_construction_guards": guards,
        "fallback_callers": callers,
        "overlay_files": {
            virtual: {
                "path": physical,
                "sha256": sha(Path(physical).read_bytes()) if physical else None,
            }
            for virtual, physical in replacements.items()
        },
        "source_unchanged": inventory
        == {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in inputs},
        "limitations": [
            "Linux SO_COOKIE and I/O, substituted JNI bind callback",
            "Actual SDK bootstrap attempts stopped after registration, before connect; no public HTTPS/DNS success",
            "Nil/default reachability guards are source inspection",
            "Original SDK test files excluded; only focused controls executed",
            "Not actual Android execution, enrollment, DE, Moto or full acceptance",
        ],
        "stack_acceptance": False,
    }
    (out / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(out / "receipt.json"),
                "exit": result.returncode,
                "source_unchanged": record["source_unchanged"],
            }
        )
    )
    raise SystemExit(result.returncode or (0 if record["source_unchanged"] else 1))


if __name__ == "__main__":
    main()
