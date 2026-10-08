# Dataset card template

Copy this file next to a corpus's `manifest.json` and fill in every section
before the corpus is shared or a result built on it is published. See
[corpus.md](corpus.md) for how the corpus is produced.

## Identity

- **Name and version**:
- **Manifest**: `manifest.json` schema `ter.corpus-manifest/1`, sha256 of the file:
- **TER version used to import**:
- **Maintainer and contact**:

## Collection

- **Who produced the sessions** (roles, how many people):
- **Period** (first and last timestamp from the manifest):
- **Agent and models** (Claude Code version, models seen in usage blocks):
- **How sessions were chosen** (all sessions in a period, a sample, hand-picked; anything excluded and why):
- **Repositories and languages**:

## Redaction and privacy

- **Policy** (copy `policy` from the manifest):
- **Redaction counts by kind** (sum over the manifest):
- **Manual review**: who spot-checked which sessions, and what they found:
- **Residual risks** (see the known limits in corpus.md):
- **Consent**: everyone whose work appears agreed to its use for:

## Licence

- **Licence of the sessions**:
- **Licence of quoted code** (only with `--quote-files`), per repository:
- **Restrictions on use**:

## Labels

- **Who labelled, and how** (guidelines, one or more raters):
- **Label coverage** (sessions with labels / total):
- **Agreement** (if more than one rater; see issue #36):

## Quality

- **Coverage** (sessions below 99%, unrecognised record types):
- **Load failures**:
- **Known gaps or biases**:

## Uses

- **Intended uses** (detector calibration, benchmarks, research questions):
- **Uses to avoid**:
- **Results that used this version**:
