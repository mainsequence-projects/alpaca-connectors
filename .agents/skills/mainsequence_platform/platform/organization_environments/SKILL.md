---
name: organization-environments
description: Understand, enumerate, design, and review Main Sequence Organization Environments and their lifecycle. Use to resolve visible environment UIDs before human or local Agent discovery; distinguish an Environment from a CodeRepositoryBranch, release, or deployment; and reason about backend-derived runtime scope for Jobs, ResourceReleases, and Harness Agents.
---

# Main Sequence Organization Environments

Use this skill to determine which Organization Environment owns an operation or
resource. Read mainsequence://platform/ontology first. Use the
code-repository-design skill when the decision belongs in a repository
Blueprint, and use the resource-release skill for deployment work.

## Canonical relation

The normalized public relation is organization_environment, its public selector
is organization_environment_uid, and collection filtering uses
?organization_environment_uid=<uid>.

An Organization Environment is the Organization-wide operational partition for
data, configuration, code execution, applications, and Agents. It is not a
CodeRepository, Git branch, release, or DeploymentRun.

Operational roots store one required Environment, descendants derive it through
their mandatory owner, declared polymorphic boundaries carry backend-maintained
projections, and retained history stores immutable snapshots. Do not add a
nullable Environment field as a fallback and do not derive Environment from the
creator alone.

## Branch mapping

One CodeRepository can participate in several Environments through sibling
CodeRepositoryBranch rows. Django owns the mapping:

    (CodeRepository.organization_owner, CodeRepositoryBranch.repository_branch)
      -> OrganizationEnvironment.required_repository_branch

The relation must satisfy both:

    CodeRepository.organization_owner == OrganizationEnvironment.organization_owner
    CodeRepositoryBranch.repository_branch == OrganizationEnvironment.required_repository_branch

The exact branch name is case-sensitive. A caller cannot create or PATCH the
branch's Environment assignment. A missing non-production mapping never falls
back to production. Signed provider pushes may create a missing branch only
when its exact Environment already exists; otherwise processing records
organization_environment_not_configured and creates nothing.

Production has exact required branch main. Organizations may define any number
of additional Environments, each with a unique exact required branch. Branches
from several repositories may share an Environment only when every branch uses
that exact required branch and every repository belongs to the same
Organization.

## Ownership graph

    Organization
    ├── OrganizationEnvironment
    │   ├── OrganizationProject
    │   ├── Secret and Constant
    │   ├── configured model-provider credentials and custom providers
    │   ├── Bucket and PVCDisk
    │   └── Agent
    └── CodeRepository
        └── CodeRepositoryBranch -> OrganizationEnvironment
            ├── Job and JobRun
            ├── project CodeRepository images
            ├── ResourceRelease and revisions
            └── one optional Harness Agent aggregate
                ├── backend-created Agent identity
                └── ResourceRelease(release_kind=harness_agent)

The Harness Agent is not a separate deployment service. Its Agent provides
semantic identity; its harness_agent ResourceRelease provides build, revision,
runtime, credential, observability, and cleanup state. One branch can own at
most one Harness Agent across all workflow files.

Every relation between two Environment-related objects must resolve the same
exact Environment. Organization equality alone is insufficient.

## Caller context

### Branch-owned runtime

Authenticated runtimes receive a backend-derived Environment ceiling:

    JobRun JWT -> JobRun -> Job -> CodeRepositoryBranch -> Environment

    ResourceRelease revision credential
      -> ResourceReleaseRevision
      -> ResourceRelease
      -> CodeRepositoryBranch
      -> Environment

    Harness Agent runtime
      -> harness_agent ResourceRelease revision credential
      -> bound ResourceRelease
      -> backend-created Agent and CodeRepositoryBranch
      -> Environment

The caller never selects runtime mode, branch, or Environment. Container
environment values and image labels are diagnostic evidence, not authority.
The authenticated principal's normal DRF, role, sharing, and operation policy
still applies; Environment scope narrows those permissions and never replaces
them.

A missing or inconsistent runtime Environment fails closed. Never fall back to
human grants, production, main, an image, or a prompt value.

### Human or local caller

A human or local process has no implicit authenticated branch. Before Agent
list or search, call organization_environment.list, follow pagination until
next is null, present the visible choices, and obtain the user's selection.
Resolve a user-supplied name through the tool instead of guessing its UID. Do
not silently default to production or the only visible row.

Reuse the selected public UID for all Environment-bounded Agent, AgentSession,
and AgentTask discovery. A local checkout may use its active Git branch to
resolve a persisted CodeRepositoryBranch for an explicit operation; it cannot
manufacture runtime identity or widen Environment scope.

## Resource boundaries

### Independent application storage

MetaTables owns its catalog, storage registration and table authorization.
Its resources have no platform Environment ownership or routing relationship.
An authenticated platform runtime context does not add a catalog scope or
select storage for that application. Use its installed application guidance
for storage operations; ordinary platform Secrets retain the policy below.

### Secrets and Constants

Every operational Secret and Constant belongs to exactly one Environment.
There is no Organization-global shadowing or effective-union lookup. Public
writes require organization_environment_uid; reads and name resolution are
constrained to that exact Environment. Environment membership alone never
grants secret-value access.

Workflow env_vars are target-owned process values. They do not create or
resolve platform Secrets or Constants and do not change branch ownership.

### Jobs, releases, and Harness Agents

Jobs, ResourceReleases, and Harness Agents remain owned by an exact
CodeRepositoryBranch. The branch supplies their Environment. Their runtime
images must carry verified provenance for the same branch and commit.

A harness_agent declaration does not accept an Environment, branch UID, Agent
UID, release kind, or FastAPI product kind. Django derives the branch and
Environment, creates the Agent identity, and persists one
release_kind=harness_agent release.

## Lifecycle

1. Organization bootstrap creates exactly one production Environment for main.
2. Organization administrators may create additional Environments with unique
   exact required branches.
3. CodeRepository creation establishes production main; an approved bootstrap
   Environment may cause its derived non-main branch to be created from main.
4. Later signed provider pushes create only branches admitted by a pre-existing
   exact Environment.
5. Repository synchronization applies branch-owned Jobs, ResourceReleases,
   Static Sites, and at most one Harness Agent.
6. Code promotion occurs through Git or CI moving code to the target
   Environment's exact branch, followed by normal target-owned automatic
   redeployment policy evaluation.

The Environment itself is never deployed and owns no DeploymentRun. Deploying
code does not copy physical data, MetaTable registrations, Secrets, Constants,
schedules, or history. Configuration and data promotion require explicit,
separately authorized workflows. Never implement promotion by rewriting
CodeRepositoryBranch.organization_environment.

## Management surface

The canonical DRF resource is OrganizationEnvironmentViewSet at
/api/v1/organization-environments/. Organization-admin permission controls
mutations. Runtime credentials may observe only their target-derived
Environment and cannot mutate this resource. The read-only
organization_environment.list MCP tool delegates to the same list action and
adds no alternate visibility policy.

OrganizationProject is a separate Command Center grouping. It can organize
branches only from its one exact Environment. Its view/edit grants cascade to
those branches and their Jobs and ordinary releases at the same level, while
Environment admission still applies. An Agent's backing release follows Agent
sharing, and a release's backing Job follows its release. Granting edit to a
workload through a Project trusts it to change code using those workloads' access.

## Stop conditions

Stop and ask for direction when:

- Organization, Environment, repository, and branch are treated as one identity;
- a caller tries to select or PATCH a branch Environment directly;
- branches with different exact names are assigned to one Environment;
- a human credential is treated as having implicit runtime Environment scope;
- a release operation is described as deploying an Environment;
- code deployment is assumed to migrate data or configuration; or
- an established branch or resource mapping would change without
  an explicit migration plan.

## Handoff

Return the Organization, exact CodeRepositoryBranch, Environment public UID and
required branch, caller context, affected resource classes, whether the work
changes code, deployment, configuration, data, or mapping, and the next owning
skill or canonical application workflow.
