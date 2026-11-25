# Contributing

Use a topic branch and keep changes focused. A pull request should explain the artist workflow affected, cover failure cases and identify what was verified. Include synthetic fixtures or instructions for local validation when useful.

Run the checks in [SETUP.md](SETUP.md) and [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md). Standard CI needs Windows, Python and Node. GPU claims require actual hardware, driver, runtime, model revision, parameters and measured evidence.

Keep user projects, models, secrets, logs and caches outside commits. New catalog entries require a real adapter, pinned artifacts, source and license review, resource assumptions and a verification label. Contributions use the license applicable to the files changed.
