This repository builds and tests the macOS (Apple Silicon) omp binary for one exact tag of the public fork EYYCHEEV/oh-my-pi on GitHub's standard macos-15 runner.
It starts only by hand (workflow_dispatch) with a tag, that tag's full commit and a list of tests, and it signs a build attestation for the binary and its build record.
It holds no secrets and writes to no other repository; SETTINGS.md lists the repository settings it needs and PINS.md lists the pinned actions.
