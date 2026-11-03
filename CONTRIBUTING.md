# Contributing

Use English for code, comments, documentation, issues, and pull requests.
Preserve raw observations exactly as captured, including the original language.

Start with `README.md`, `PROTOCOL.md`, and `EVIDENCE.md`. Use a research issue
for a protocol concern or an independent reproduction; use a bug report for a
software defect. Include the commit, environment, exact command, and observed
result. Keep sensitive data out of public reports.

## Changes and evidence

- Work on a branch and open a pull request. The default branch requires the
  `Research checks` status and resolved review conversations. A solo maintainer
  can merge without inventing an independent approval.
- Use concise English commits and add a Developer Certificate of Origin sign-off
  with `git commit -s`. The sign-off certifies the contribution under
  <https://developercertificate.org/>. GitHub web commits also require sign-off.
- Keep original source and documentation under Apache-2.0. Preserve third-party
  attribution and license boundaries described in `THIRD_PARTY_NOTICES.md`.
- Do not overwrite an earlier run, remove failures to improve an outcome, or
  change a registered method without recording the deviation. New measurements
  belong in a new run directory with the executed source checksum.
- Separate model judgements from human review and emulator results from real
  inference. Report all comparable arms and the limits of the evidence.

Run the README's tests before opening a pull request. CI checks the code and
published checksums; it does not rerun the model or Kubernetes campaign and is
not scientific validation of the conclusions.

## Research snapshot

Preserve scientific source and data identities in the evidence manifests.
Follow active branch protections. New experiments require separate immutable
observation files.

## GitHub configuration

`.github/repository.json` records repository settings, topics, and custom labels.
`.github/rulesets/` records the active default-branch and tag protections.
Keep these files aligned with the live configuration. Dependency updates are
reviewed as source changes and do not retroactively alter a recorded experiment.
