# Domain module — software delivery

**Trigger:** the work ships installable, versioned software that other people run.

If that is not your work, skip this module. The spine already covers you, and
`domains/README.md` section 3 maps common non-software work onto it.

## What this module adds

Eight standards covering the machinery that only exists when you ship software: source change
practice, verification evidence, version contracts, integration control, the release pipeline,
rollback and hotfix, dependencies, and what the project presents to the outside world.

| Standard | Covers |
|---|---|
| `change-and-code-practice.md` | How changes are made and structured |
| `verification-and-testing-evidence.md` | What evidence each change class requires |
| `versioning-compatibility-deprecation.md` | The promise a version number makes |
| `integration-and-merge-control.md` | Getting a change into the shared line |
| `release-pipeline-and-log.md` | Stages, gates, and the append-only record |
| `rollback-and-hotfix.md` | When a release is wrong |
| `dependencies-and-supply-chain.md` | Code you did not write |
| `documentation-and-presentation.md` | What a user or contributor encounters |

## What it does not add

No new protected categories. No new authority values. The irreversible actions of software delivery
map onto spine control classes in `instances.yaml` — publishing a package is
`publish.external-irreversible`, merging to mainline is `integrate.shared-baseline`, deleting a
release tag is `record.history-alter`.

If you find yourself wanting an exemption here that the spine does not allow, the answer is not in
this module.
