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

The project license has not yet been selected. Do not submit or redistribute code under
the assumption that this repository already grants a license. Third-party binaries and
sources require provenance, notices and redistribution review.
