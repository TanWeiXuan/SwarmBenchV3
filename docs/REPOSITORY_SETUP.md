# Repository setup not performed by code

The workflows are implemented but administrators must configure GitHub:

1. Enable Discussions and create a category named **Tournament Results** with slug `tournament-results`.
2. Protect `main` with required checks `Test suite` and `Submission Gate`, pull requests, deletion/non-fast-forward blocking, and squash-only merges. Enable auto-merge and delete branches after merge.
3. Install a GitHub App on this repository. It needs Contents and Pull requests write access for current-state PRs; media publication needs Contents write; the workflow token needs Discussions write where declared.
4. Set Actions variable `SWARMBENCH_APP_ID` and Actions secret `SWARMBENCH_APP_PRIVATE_KEY`. Secret values cannot be copied through the GitHub API.
5. Keep workflow default permissions read/write and allow workflows to approve/merge PRs as required by repository policy. Untrusted jobs still override this with `contents: read` only.
6. Ensure hosted/self-hosted runners provide Docker, Python 3.12, FFmpeg with libx264 and FFprobe.

Remote Discussion creation, App-token PR publication and Release asset upload require those credentials and are intentionally not exercised by local tests. Missing remote configuration does not prevent local tests, replays, Docker matches or exhibition tournaments.
