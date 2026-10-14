# Security Policy

## Supported versions

Security updates are provided for the latest public release line.

| Version | Supported |
| --- | --- |
| 2.x | Yes |
| Earlier internal iterations | No |

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use the repository host's private security-advisory mechanism when available, and include:

- the affected TER version;
- reproduction steps or a minimal sample;
- expected and observed behavior;
- potential impact;
- any suggested mitigation.

Avoid attaching private Claude session data. Replace sensitive prompts, outputs, paths, credentials, and identifiers with a minimal synthetic reproduction.

## Generated HTML reports

TER HTML reports embed analyzed session content. Treat generated reports as potentially sensitive artifacts. Review them before sharing, store them with access controls appropriate for the underlying session, and do not publish reports containing confidential prompts or model outputs.

## Software bill of materials

Every CI run publishes a CycloneDX SBOM of the wheel it built as the `sbom` artifact of the run: each distribution an install of TER brings, with its exact version, package URL and declared licence. Feed it to a scanner such as `grype sbom:ter.cdx.json` or OSV-Scanner to check the dependencies for known vulnerabilities. `python dev.py sbom` writes the same file from a checkout. See the [testing guide](docs/guides/testing.md#software-bill-of-materials).
