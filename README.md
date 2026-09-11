# Finance Capybara infrastructure

The ticker ingestion pipeline's infrastructure and Lambda deployment,
provisioned directly via `infra/deploy.py` (boto3) and run by GitHub Actions
in `us-east-1` — no Terraform or other IaC tool. All explicitly named AWS
resources start with `fcb-`; the KMS alias uses AWS's required `alias/`
namespace.

| Resource | Deployed name |
| --- | --- |
| KMS key (Name tag / alias) | `fcb-master-key` / `alias/fcb-master-key` |
| Enabled ticker bucket | `fcb-enabled-tickers` |
| Pivoter Lambda / role | `fcb-pivoter` / `fcb-pivoter-role` |
| SQS queue | `fcb-inbound-ticker-interest` |
| Aggregator Lambda / role | `fcb-aggregator` / `fcb-aggregator-role` |
| Daily runs bucket | `fcb-aggregated-daily-runs` |
| Lambda log groups | `fcb-pivoter-logs`, `fcb-aggregator-logs` |
| Outbound SMS topic | `fcb-outbound-ticker-notification` |
| Pivoter daily schedule | `fcb-pivoter-daily` (EventBridge rule) |

The inconsistent `fc`, `fcb0-enabled tickers`, `aggreggated`, and `agregattor`
spellings in the request are normalized above. If an S3 name is already owned
by another account, set the GitHub variable `S3_BUCKET_NAME_SUFFIX` to a suffix
such as `-123456789012`. Only bucket names receive that suffix; all references
and permissions follow the resulting names.

## Resources and permissions

Both buckets block public access, disable ACLs, enable versioning and use the
rotating master KMS key. The queue also uses that key and retains messages for
14 days. `deploy.py` never deletes buckets, roles, or the queue — only
creates them if missing and updates their configuration to match the script.

The pivoter role can list and read the enabled ticker bucket, send messages to
the inbound queue, and use `kms:Decrypt`, `kms:Encrypt`, and
`kms:GenerateDataKey` on the master key only. The aggregator role can receive,
delete, inspect and extend visibility of inbound messages, write to the daily
runs bucket (including aborting incomplete multipart uploads), publish to the
outbound notification topic, and use the same three KMS operations on that
key. Each role can write only to its own log group. Neither role can assume
the other role or administer KMS, S3, SQS or IAM.

S3 uploads must explicitly request SSE-KMS with the master **key ARN**. For example,
with the deployed `kms_key_arn` output in `KMS_KEY_ARN`:

```sh
aws s3 cp tickers.json s3://fcb-enabled-tickers/tickers.json \
  --sse aws:kms --sse-kms-key-id "$KMS_KEY_ARN"
```

Application `put_object` calls must supply `ServerSideEncryption="aws:kms"`
and `SSEKMSKeyId=os.environ["KMS_KEY_ARN"]`. S3 handles decryption on reads and SQS
handles message encryption transparently, using the caller's IAM permissions.
The uploader needs separate S3 write and KMS permissions; pivoter deliberately
cannot upload enabled ticker files.

## Lambda implementation boundary

Both functions are implemented. `infra/lambda/pivoter/` on each invocation
lists every object in `ENABLED_TICKERS_BUCKET`, and for each one fetches
recent daily prices via `yfinance`, computes a simple moving average and
checks for a recent downward-to-upward trend reversal (a "pivot"). Each
enabled-ticker object is a small JSON document:

```json
{"ticker": "AAPL", "sma_window": 20, "lookback_days": 30}
```

`lookback_days` is optional (defaults to 30). When a ticker shows a pivot
within the lookback window, pivoter publishes to `INBOUND_QUEUE_URL`:

```json
{"datetime": "2026-09-07T14:32:01.123456+00:00", "ticker": "AAPL", "pivot_date": "2026-08-25"}
```

A failure fetching or analyzing one ticker is caught and recorded rather than
aborting the whole run; the function returns `{"checked", "pivots_detected",
"errors"}` for each invocation.

Pivoter runs automatically once a day via the `fcb-pivoter-daily` EventBridge
rule (`ensure_pivoter_schedule()` in `deploy.py`): `cron(0 22 * * ? *)`, i.e.
19:00 `America/Sao_Paulo` (UTC-3) / 22:00 UTC. A fixed UTC cron is exact here
because Brazil has not observed DST since 2019 — there's no seasonal offset
to account for. Classic EventBridge Rules don't support IANA timezones
directly (EventBridge Scheduler does), which is why the UTC time is computed
by hand in the one place it's defined rather than expressed as a local time.

`infra/lambda/aggregator/` is wired to `INBOUND_QUEUE_URL` via an SQS event
source mapping (`ensure_sqs_trigger()` in `deploy.py`), so it runs whenever
pivoter publishes a pivot. Each invocation receives a batch of up to 10
messages (SQS's default `BatchSize`), parses each one (skipping and logging
any that fail to parse rather than failing the whole batch), and if at least
one parses:

1. Writes the batch as a single Parquet file to `AGGREGATED_RUNS_BUCKET`,
   partitioned by the **processing date** (UTC, when the Lambda runs — not
   each message's own `pivot_date`) as
   `year=YYYY/month=MM/day=DD/HHMMSS-<uuid>.parquet`. Many small files per
   day is the normal shape for this pattern (no read-modify-write races);
   Athena/Glue can query a whole day's partition regardless of file count.
2. Publishes a summary (`"N pivot(s) detected: TICKER1, TICKER2, ..."`) to
   `OUTBOUND_TOPIC_ARN` (`fcb-outbound-ticker-notification`), an SNS topic
   with an SMS subscription to the phone number in `NOTIFICATION_PHONE_NUMBER`.
   `deploy.py` requires this (as `--notification-phone-number` or the env
   var) for every run except `--ecr-repo-only`; in CI it comes from the
   `NOTIFICATION_PHONE_NUMBER` secret.

If a batch has no parseable messages, nothing is written and no notification
is sent — nothing worth reporting happened.

Both functions receive `KMS_KEY_ARN` and `INBOUND_QUEUE_URL`. Pivoter also
receives `ENABLED_TICKERS_BUCKET`; aggregator receives
`AGGREGATED_RUNS_BUCKET` and `OUTBOUND_TOPIC_ARN`.

`pyarrow` (for Parquet) makes aggregator's zip package ~41MB compressed —
comfortably under the 250MB uncompressed Lambda limit, but close enough to
the 50MB direct-`ZipFile`-upload API limit that `build_lambda_zip()` in
`deploy.py` raises a clear error above 45MB rather than let it fail
opaquely at the API layer. If a future dependency bump crosses that line,
switch to S3-based Lambda code upload (`S3Bucket`/`S3Key`) instead of
`ZipFile`.

### Pivoter packaging: container image

`yfinance` and `pandas` are too large to fit a zip-based Lambda package
reliably under AWS's 250MB uncompressed size limit (confirmed by measuring
the real dependency tree, including AWS's own managed pandas layer — it
lands right at the ceiling with no safe margin). Pivoter is therefore
packaged as a container image and deployed to the `fcb-pivoter` ECR
repository (`ensure_ecr_repository()` in `infra/deploy.py`), built from
`infra/lambda/pivoter/Dockerfile`. `aggregator` stays a plain zip package,
built in-memory from `infra/lambda/aggregator/` by `zip_directory()`.

The deploy workflow builds and pushes the image before running the main
script, tagged with the commit SHA. This is a two-phase process: the ECR
repository is created first (`python deploy.py --ecr-repo-only`) so there's
somewhere to push to, since the image must already exist in ECR before
Lambda can create or update a function pointing at it. If `fcb-pivoter` is
ever found with `PackageType` other than `Image` (e.g. after being
provisioned some other way), `ensure_image_function()` deletes and recreates
it — AWS does not allow `package_type` to change in place. Nothing currently
invokes pivoter automatically, so the brief gap during replacement is
low-risk.

## GitHub deployment

`.github/workflows/deploy.yml` runs on every **push to `main`**, including a
merge or a direct push. A job-level condition also enforces the event and
branch; there is no manual deployment entry point and no deployment on
`master`, `develop`, feature branches or PR events. It authenticates to AWS
via GitHub OIDC (no long-lived credentials), then:

1. Ensures the `fcb-pivoter` ECR repository exists
   (`python deploy.py --ecr-repo-only`), so there's somewhere to push to.
2. Builds and pushes the pivoter image for `linux/arm64`, tagged with the
   commit SHA (via QEMU + Buildx, since the runner is x86_64).
3. Runs `python deploy.py --pivoter-image-uri <repo>:<sha>`, which
   provisions everything (KMS key, S3 buckets, SQS queue, both Lambda IAM
   roles, both log groups) and creates or updates both Lambda functions.

`.github/workflows/validate.yml` runs on PRs targeting `main`: it checks
`deploy.py`'s syntax and runs the pivoter Lambda's pytest suite. It does not
touch AWS — there's no dry-run/plan equivalent for this scripted approach,
so a PR only proves the Python is well-formed and the business logic is
correct, not that the AWS calls will succeed. Review deploy.yml diffs
carefully for that reason.

The deployment role is `arn:aws:iam::545978922966:role/pivoter-github-actions-deployer`;
its ARN is the `production` environment's `AWS_ROLE_TO_ASSUME` secret, and
that environment is configured to allow only the branch `main`. Its trust
and permissions policies are **not** managed by `deploy.py` — a role can't
grant itself IAM permissions it doesn't already have, so self-managing it
from CI would either be impossible on the first grant or a privilege-escalation
risk on every later one. Its policy documents stay as the standalone,
human-reviewable JSON files `.github/aws-deployer-trust-policy.json` and
`.github/aws-deployer-permissions-policy.json`; apply changes to the live
role manually (e.g. `aws iam put-role-policy`) before merging a PR that
needs them, or the next deploy will fail with `AccessDenied` on whatever
action was newly added. The policy limits application resource management
to the requested `fcb-` buckets, queue, functions, roles, logs, tagged KMS
key, the `fcb-pivoter` ECR repository, the `fcb-outbound-ticker-notification`
SNS topic and the `fcb-pivoter-daily` EventBridge rule. The Lambda
event-source-mapping actions
(`Create`/`Delete`/`Get`/`List`/`UpdateEventSourceMapping`, for wiring the
queue to aggregator) are the one exception scoped to `Resource: "*"` rather
than a specific ARN — the mapping's own ARN includes a UUID that doesn't
exist until after creation, so it can't be pre-scoped like everything else
here.

`deploy.py` also needs a `NOTIFICATION_PHONE_NUMBER` secret: the E.164 phone
number (e.g. `+15551234567`) subscribed to the outbound SNS topic for pivot
alerts. Never commit a real phone number to this repo; it's PII and belongs
only in the GitHub secret and whatever local env var you use for a manual
run.

Because the job retains `environment: production`, the trust policy uses
these exact conditions:

```json
{
  "StringEquals": {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub": "repo:GBarbosa1@97404958/finance-capybara-infra@1343760649:environment:production"
  }
}
```

In **Settings → Environments → production → Deployment branches and tags**,
allow only the **branch** `main` (no tags). This setting is essential: an
environment-based OIDC subject does not itself identify the branch. The owner
and repository IDs in the subject match this GitHub account's customized OIDC
subject format and prevent a renamed or replaced repository from inheriting
deployment access.

If an older experimental DynamoDB/queue configuration from
`feature/first-deploy` still exists in this account, `deploy.py` never
touches it — it only reads and writes the specific `fcb-*` resource names
listed above.

## Main branch and your approval

`main` has been created from the existing remote `master` baseline and is now
the default branch. Its GitHub protection rule requires one code-owner approval
and dismisses stale approvals, with administrator bypass enabled. The specific
code-owner assignment takes effect once `.github/CODEOWNERS` is on `main`.
The local scaffold is on `codex/aws-scaffold`, based on `main`. These file changes
are uncommitted; no new code commits or AWS resources were deployed during setup.

Before enabling the first deployment:

1. Add the missing `AWS_ROLE_TO_ASSUME` secret and verify the AWS role's OIDC
   trust, permissions, and access to the existing state bucket described above.
2. Commit and publish the scaffold branch for review. Its deployment workflow
   activates when these files reach `main`. Preserve the `production` branch restriction.
3. Include `.github/CODEOWNERS` on `main`. It assigns **every file to `@GBarbosa1`**.
4. Preserve the configured protection for `main`: one approving review,
   required code-owner review and dismissal of stale approvals. Leave
   administrator bypass enabled
   so you can push directly to `main`. Do not enable force pushes or deletion.
5. Keep yourself as the only administrator if only you should bypass. GitHub's
   administrator exemption also applies to any other repository administrator.

The exact classic branch-protection API body is checked in as
`.github/main-protection.json`. After `main` exists, an authenticated repository
administrator with GitHub CLI can apply it from the repository root:

```sh
gh api --method PUT repos/GBarbosa1/finance-capybara-infra/branches/main/protection \
  --input .github/main-protection.json
```

`CODEOWNERS` alone requests reviews; GitHub branch protection enforces them.
The saved rule and the CODEOWNERS file together require your approval for other
contributors' PRs while preserving your direct push access. GitHub does not allow authors to
approve their own PRs; use your administrator bypass for your own changes.
Administrator bypass can also bypass PR review requirements when you choose to
use it.

## Local verification (no AWS deployment)

```sh
python -m py_compile infra/deploy.py
pip install -r infra/lambda/pivoter/requirements-dev.txt
pytest infra/lambda/pivoter
pip install -r infra/lambda/aggregator/requirements-dev.txt
pytest infra/lambda/aggregator
```

There's no dry-run mode for `deploy.py` — unlike `terraform plan`, boto3
calls don't have an equivalent preview. `deploy.py` is written to be
idempotent (describe-or-create, then always reapply configuration/tags), so
re-running it against real AWS is safe, but there's no way to preview a run
locally without credentials that can actually touch the `fcb-*` resources.
If you have such credentials, run it directly:

```sh
python infra/deploy.py \
  --pivoter-image-uri <account>.dkr.ecr.us-east-1.amazonaws.com/fcb-pivoter:<tag> \
  --notification-phone-number +15551234567
```

Omit `--pivoter-image-uri` to provision/update everything except the
pivoter Lambda itself (useful when you don't have an image built yet — see
`--ecr-repo-only` below). `--notification-phone-number` is still required in
that case; it (or `NOTIFICATION_PHONE_NUMBER` in the environment) is needed
for every run except `--ecr-repo-only`.

## No Terraform: what changed and why

This infrastructure was previously Terraform-managed (state in
`terraform-state-545978922966`, `finance-capybara-infra/terraform.tfstate`).
It moved to a plain boto3 script because Terraform's `package_type`-replace
semantics for the pivoter Lambda, combined with the deployer role's
self-referential IAM bootstrap problem (a role can't grant itself
permissions it doesn't already have — true regardless of tool, but
Terraform's own apply-time self-management made it a recurring blocker
instead of a one-time manual step) and this environment's restrictions on
running `terraform apply` interactively, made the deploy loop too slow to
iterate on. The old Terraform state file in the state bucket is no longer
referenced by anything and was left as-is rather than deleted — the actual
AWS resources it described continue to exist and are now the source of
truth `deploy.py` describes-and-reconciles against directly, not something
recreated from scratch.

References: [SQS KMS permissions](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-key-management.html),
[S3 SSE-KMS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html),
[GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches),
[boto3 Lambda client](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/lambda.html).
