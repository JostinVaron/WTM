# GitHub publication and secure owner authentication

## Proposed repository

- Repository name: `wireless-tactile-monitor`
- Description: `Technical documentation and measured prototype status for a touch-enabled Android virtual display stream.`
- Visibility: public, because the stated goal is for other developers to learn and reproduce the research.
- Initial content: documentation, requirements, issue templates, governance and security policy only.
- No APK, no runtime source snapshot, no scrcpy/R8/Android JARs, no logs, no certificates,
  no private keys, and no ADB authorization material.

The runtime is not ready to publish: it has an unauthenticated control API and the source
project has no selected license or complete third-party notice. Do not disable the
repository security guard or add runtime files just to enable a release.

## Why GitHub credentials are needed

The GitHub MCP tools available in this environment provide read/search operations only.
The integrated browser is signed out, and the GitHub CLI is not installed or
authenticated. An email address is not sufficient to identify a GitHub username or
authorize repository creation.

Never send a GitHub password, personal access token, recovery code, SSH private key or
ADB key in this chat. The account owner should authenticate through GitHub's official
OAuth/device flow. On a machine where the owner is comfortable storing GitHub CLI
credentials:

```sh
gh auth login --hostname github.com --git-protocol https --web
gh auth status
gh api user --jq .login
```

The owner completes the browser/device authorization directly on GitHub. Do not paste
the password or access token into a terminal command, repository file, issue, or chat.
Review where the GitHub CLI stores credentials on that host; use its OS credential
store/keyring when available and protect any local fallback configuration.

After confirming the authenticated login is the intended account, initialize and push
the prepared documentation tree:

```sh
cd WTM-Project
git init -b main
git add .
git diff --cached --check
git commit -m "docs: establish WTM technical handoff"
gh repo create wireless-tactile-monitor \
  --public \
  --description "Technical documentation and measured prototype status for a touch-enabled Android virtual display stream." \
  --source=. \
  --remote=origin \
  --push
```

The command above should only be run after `gh api user --jq .login` confirms the owner
account. If the repository name already exists under that owner, inspect it before
choosing a different name; do not overwrite or force-push an existing project.

## Initial issues and releases

The repository includes issue forms, but no issues should be fabricated. Create initial
issues after publication for:

1. secure/authenticated control plane;
2. Termux-independent service feasibility and Android permission model;
3. reproducible install/dependency manifest;
4. sustained high-motion decoder/performance investigation;
5. app-switch freeze/visual-artifact test plan;
6. lifecycle/reconnect tests after reboot, force-stop and Wi-Fi changes;
7. project license and third-party notice review.

There should be no GitHub Release until the gate in
[project governance](project-governance.md) is satisfied. A documentation snapshot is
not a downloadable software release.
