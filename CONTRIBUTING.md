# Contributing

WTM is an experimental Android display-streaming prototype. Contributions should
preserve the distinction between measured behavior, configuration, hypotheses and future
goals.

## Before opening an issue or pull request

- Search existing issues and read the architecture, requirements and performance docs.
- Do not include real device serials, IPs, account data, pairing keys, TLS keys or
  unredacted logs.
- Do not claim “2K60”, “20 ms”, “OLED quality”, “restart-proof” or “DeX compatible”
  without stating the exact device, workload, duration and measurement method.
- Keep changes small and test the active Termux/scrcpy path separately from historical
  Xvfb/PRoot experiments.

## Performance changes

Report source FPS, browser input FPS, decoded FPS, dropped frames, queue depth, latency
distribution, network conditions, thermal state and test duration. A still screen is not
a valid FPS workload.

## Licensing

Software is licensed under [PolyForm Noncommercial 1.0.0](../LICENSE) and
documentation under [CC BY-NC 4.0](../LICENSE-docs.txt); see
[Licensing](../LICENSING.md). Both permit non-commercial use, modification and
sharing with attribution. Do not add third-party binaries or sources without
provenance, notices and redistribution review.

## Running the tests

The runtime tests run on any host with Python 3.10+ and aiohttp:

```sh
cd runtime/termux
python3 -m pip install -r requirements.txt
python3 -m unittest tests.test_server_security tests.test_server_live
```

The device certification script must run in Termux on the source phone after
`install.sh`:

```sh
cd ~/dex
bash tests/device-smoke.sh
```

See [certification](docs/certification.md) for what each suite covers and
[AGENTS.md](../AGENTS.md) for the rules AI assistants must follow.

## AI-assisted contributions

AI-generated analyses and patches are welcome if they follow
[AGENTS.md](../AGENTS.md): cite file/line evidence against the current tree,
state which checks were actually executed, and record dated review entries in
[docs/certification.md](docs/certification.md) when publishing an independent
review. Reviews based on stale snapshots or without verification will be
corrected or rejected.
