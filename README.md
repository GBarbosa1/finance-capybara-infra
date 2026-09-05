# Finance Capybara infrastructure

Terraform scaffold for the ticker ingestion pipeline, deployed by GitHub Actions
in `us-east-1`. All explicitly named AWS resources start with `fcb-`; the KMS
alias uses AWS's required `alias/` namespace.

| Resource | Deployed name |
| --- | --- |
| KMS key (Name tag / alias) | `fcb-master-key` / `alias/fcb-master-key` |
| Enabled ticker bucket | `fcb-enabled-tickers` |
| Pivoter Lambda / role | `fcb-pivoter` / `fcb-pivoter-role` |
| SQS queue | `fcb-inbound-ticker-interest` |
| Aggregator Lambda / role | `fcb-aggregator` / `fcb-aggregator-role` |
| Daily runs bucket | `fcb-aggregated-daily-runs` |
| Lambda log groups | `fcb-pivoter-logs`, `fcb-aggregator-logs` |

The inconsistent `fc`, `fcb0-enabled tickers`, `aggreggated`, and `agregattor`
spellings in the request are normalized above. If an S3 name is already owned
by another account, set the GitHub variable `S3_BUCKET_NAME_SUFFIX` to a suffix
such as `-123456789012`. Only bucket names receive that suffix; all references
and permissions follow the resulting names.

## Resources and permissions

Both buckets block public access, disable ACLs, enable versioning and use the
rotating master KMS key. The queue also uses that key and retains messages for
14 days. Nonempty buckets are not automatically emptied by Terraform.

The pivoter role can list and read the enabled ticker bucket, send messages to
the inbound queue, and use `kms:Decrypt`, `kms:Encrypt`, and
`kms:GenerateDataKey` on the master key only. The aggregator role can receive,
delete, inspect and extend visibility of inbound messages, write to the daily
runs bucket (including aborting incomplete multipart uploads), and use the same
three KMS operations on that key. Each role can write only to its own log group.
Neither role can assume the other role or administer KMS, S3, SQS or IAM.

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

`infra/lambda/pivoter/handler.py` and `infra/lambda/aggregator/handler.py` are
packaged automatically. They deliberately raise `NotImplementedError` until
the business logic is supplied, so unprocessed work cannot look successful.
No S3 notifications, schedules, public endpoints or SQS event source mappings
are created by this scaffold. Add triggers after implementing and testing the
message format and processing logic. The queue's visibility timeout is already
six times the aggregator timeout for a future SQS mapping.

Both functions receive `KMS_KEY_ARN` and `INBOUND_QUEUE_URL`. Pivoter also receives
`ENABLED_TICKERS_BUCKET`; aggregator receives `AGGREGATED_RUNS_BUCKET`.

## GitHub deployment

The workflow in `.github/workflows/deploy.yml` is adapted from the existing
`feature/first-deploy` workflow. It retains:

- Environment: `production`.
- Secrets: `AWS_ROLE_TO_ASSUME` and `TF_STATE_BUCKET`.
- Region: `us-east-1`.
- State object: `finance-capybara-infra/terraform.tfstate` in the existing state bucket.
- AWS authentication through GitHub OIDC, encrypted state, S3 state locking and
  serialized deployments.

Deployment runs only on a **push to `main`**, including a merge or your direct
push. A job-level condition also enforces the event and branch. There is no
manual deployment entry point and no deployment on `master`, `develop`, feature
branches or PR events. PRs targeting `main` run formatting, validation and mocked
Terraform tests without AWS credentials. All pushes to `main` run validation,
the same tests, a plan and application of that saved plan.

`TF_STATE_BUCKET` is configured in the `production` environment as
`terraform-state-545978922966`. The existing deployment role is
`arn:aws:iam::545978922966:role/pivoter-github-actions-deployer`; set that ARN as
the environment secret `AWS_ROLE_TO_ASSUME`. The `production` environment is
configured to allow only the branch `main`.

The state bucket and deployment IAM role already exist. The role's previous
trust and inline policy target the older `pivoter` repository, state path and
single Lambda. Replace them with the checked-in
`.github/aws-deployer-trust-policy.json` and
`.github/aws-deployer-permissions-policy.json`. The new policy limits application
resource management to the requested `fcb-` buckets, queue, functions, roles,
logs and tagged KMS key. It grants state access only to
`finance-capybara-infra/terraform.tfstate` and its lock file in the provided
state bucket. If that bucket uses a customer KMS key, grant access to that key
separately. The application master key is created after backend initialization.

The state bucket was verified in `us-east-1` with versioning enabled and default
SSE-S3 encryption. Its existing safeguards provide recoverable state history;
the deployment policy does not require access to a separate state KMS key.

Because the job retains `environment: production`, the replacement trust policy
uses these exact conditions:

```json
{
  "StringEquals": {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub": "repo:GBarbosa1/finance-capybara-infra:environment:production"
  }
}
```

In **Settings → Environments → production → Deployment branches and tags**,
allow only the **branch** `main` (no tags). This setting is essential: an
environment-based OIDC subject does not itself identify the branch.

If the state path already manages the older experimental DynamoDB/queue
configuration from `feature/first-deploy`, review a state-backed plan and migrate
or import those resources before the first deployment. This scaffold describes
the newly requested topology and does not silently transfer the old addresses.

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

Use Terraform 1.13.0, matching both workflows. Commit `infra/.terraform.lock.hcl`.
The lock file includes Linux and Windows provider checksums.

```sh
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra validate
terraform -chdir=infra test
```

The tests use a mocked AWS provider to check encryption, naming, role assignment
and the permissions separating the two workers. The archive provider builds
local deployment ZIPs. No AWS resources are created by these tests. State,
local settings, plans and generated ZIPs are ignored by Git.

References: [SQS KMS permissions](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-key-management.html),
[S3 SSE-KMS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html),
[Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3),
[GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
