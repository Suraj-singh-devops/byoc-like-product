# ADR 0002: Elasticsearch distribution, exact version pinning and licensing review

- Status: Accepted for the prototype. The licensing question is open and must be resolved by
  legal review before any customer deployment or commercial launch.
- Date: 2026-09-27
- Related: PRD v2 §6.2 and §18 (Elasticsearch licensing risk); TRD v2 §9 and §38

## Context

The product operates Elasticsearch for customers, on VMs in the customer's GCP project. Whether
that is permitted, and under which license and commercial terms, depends on the distribution we
install and on how the license treats "managed service" use. The PRD requires the exact
distribution and license to be recorded before implementation and reviewed before commercial
release. The TRD forbids `latest`, requires a version catalog and asks us to document the
package source and how packages are verified.

This record is an engineering summary to prepare the legal review. It is not legal advice, and
nothing in the implementation assumes an outcome of that review.

## Options

The facts below are as understood on 2026-09-27. Legal must verify them against the license
texts shipped in the package and Elastic's current licensing documentation.

| Option | What it is | Implications for a managed-service business |
|---|---|---|
| A. Elastic's default distribution (official packages) | The binaries Elastic publishes in its APT, YUM, archive and container repositories. The Debian package index for 9.5.4 declares `License: Elastic-License` and `Conflicts: elasticsearch-oss`. Elastic's license is the Elastic License 2.0 (ELv2). | ELv2 limits providing the software to third parties as a hosted or managed service that gives access to a substantial set of its features, forbids circumventing license-key functionality and forbids removing notices. Whether operating it inside the customer's own project, on the customer's infrastructure, falls under the managed-service limitation is the central legal question. A commercial agreement with Elastic (OEM, reseller or partner) may resolve it. |
| B. Build from source under AGPLv3 | Since 2024 Elastic offers the Elasticsearch source code under AGPLv3 as a third option, alongside SSPL and ELv2. | We would build, sign, host and patch our own binaries. AGPL requires offering corresponding source to users who interact with a modified version over a network. Legal must confirm which parts of the source tree each option covers. It grants no trademark rights, so our build could not be called "Elasticsearch" without permission. |
| C. SSPL | The Server Side Public License, the other source option. | Offering the program as a service requires releasing the source of the whole service stack (management software, UI, APIs, automation, monitoring, backup) under the SSPL. Almost certainly incompatible with a proprietary control plane. |
| D. Commercial agreement with Elastic | An OEM, reseller or partner contract. | Removes the ambiguity at a cost; may impose pricing, support and branding terms. |
| E. Customer-licensed model | The customer obtains Elasticsearch (and any Elastic subscription) directly from Elastic; we only automate it in their project. | Changes the go-to-market and support model; legal must confirm it actually changes who "provides" the software. |
| F. OpenSearch | Apache-2.0 fork of Elasticsearch 7.10.2, governed by the OpenSearch Software Foundation (Linux Foundation) since 2024. | No managed-service restriction. It is explicitly out of the MVP engine scope (PRD §7), its API has diverged from Elasticsearch 8 and 9, and customers who asked for Elasticsearch may not accept it. The `DatabaseProvider` abstraction allows adding it later. |

Separately from the software license, "Elasticsearch" and the Elastic logos are trademarks of
Elasticsearch B.V. Product names, UI text and marketing ("managed Elasticsearch") need their own
review whatever the license outcome.

The prototype relies only on features that Elastic's default distribution provides without a
paid subscription (security with authentication, authorization and TLS). It never installs,
modifies, disables or works around license keys.

## Decision

1. **Prototype distribution.** Elastic's default distribution, installed from Elastic's official
   APT repository (`https://artifacts.elastic.co/packages/9.x/apt`), for development and testing
   in platform-owned test projects only. No customer deployment and no commercial offering until
   the review below is complete.
2. **Exact version.** The prototype installs Elasticsearch **9.5.4**, the newest patch release of
   the newest 9.x line in Elastic's APT repository when this decision was made (repository
   release date 2026-09-15), and the version the agent's integration tests ran against. No
   `latest`, no minor-line wildcard (`9.5.*`), no automatic upgrade.
3. **Version catalog.** Installable versions live in
   `backend/app/providers/database/elasticsearch/versions.yaml`. Each entry records:
   - the exact version and its status (`supported`, `deprecated` or `withdrawn`);
   - the distribution identifier (`elastic-default` for option A);
   - the package source, the signing-key URL and fingerprint, and the SHA-256 of the package for
     each CPU architecture;
   - the license review status (`pending`, `approved` or `rejected`) and a pointer to this
     record.

   `ELASTICSEARCH_VERSION` selects the default for new clusters and must name a `supported`
   catalog entry. `ELASTICSEARCH_VERSION_CATALOG` can point to a reviewed replacement file (for
   example a Kubernetes ConfigMap) without rebuilding the image. Changing the version or
   distribution after the licensing review is a catalog change plus tests, not a code change
   (unless the distribution needs a different installer, as OpenSearch would).
4. **Verification.** The VM trusts Elastic's repository only after checking that the downloaded
   signing key has the catalog's fingerprint (`46095ACC8548582C1A2699A9D27D666CD88E42B4`); APT
   then verifies the signed repository metadata. The downloaded package is also compared with
   the catalog's SHA-256 for its architecture before it is installed:

   | Architecture | Package | SHA-256 |
   |---|---|---|
   | amd64 | `elasticsearch-9.5.4-amd64.deb` | `9530a71cabc0e47e895023f32eb0e587bedd9f03ff3d0f5ab05de0534c3b52a7` |
   | arm64 | `elasticsearch-9.5.4-arm64.deb` | `155d265ec0b855998288b646069d451b822235caf38e5662e6a974450abef9eb` |

5. **No licensing assumptions in code.** The code reads the catalog; it does not encode any
   conclusion about the license. `license.review_status` is shown by the engines API so that the
   status is visible, and it stays `pending` until legal records a decision here.

## Requires legal review before commercial launch

1. Whether operating Elastic's default distribution in the customer's project, managed by us,
   is a "hosted or managed service" under ELv2, and what agreement with Elastic, if any, is
   needed.
2. Who the licensee is (the customer or us) and which terms must be passed through to customers.
3. If option B is considered: AGPL source-offer obligations, which modules are covered, and the
   cost of building, signing, patching and supporting our own binaries.
4. Trademark use of "Elasticsearch" in the product name, UI, documentation and marketing.
5. Notice and attribution obligations for anything we bundle (agent, configuration, plugins).
6. Whether OpenSearch should be offered instead of, or alongside, Elasticsearch.
7. Confirmation that the prototype's own development and testing use is covered.

## Consequences

- The catalog is the single place to change version or distribution after the review.
- Moving to a new Elasticsearch version needs a new catalog entry with its checksums, a mock-mode
  test run and a real-cluster test before it is marked `supported`.
- Clusters on a version that is later `withdrawn` keep running but cannot be scaled until an
  upgrade path exists (upgrades are out of the MVP).
- If the review rejects option A, the platform needs either a different distribution (option B)
  or a different engine provider (option F); the provider abstraction and the catalog keep that
  change out of the core services.
