# Consumer input integrity review (2026-11-03)

Base: `b49455650eb809b3cba9aa16b8a1cd8bbcd7bfd8`.

## Contract

Read the supplied manifest once, verify every listed file against its SHA-256,
and copy those exact bytes into an isolated temporary snapshot. Both actual
consumer loaders and the resulting report use that snapshot and the captured
manifest. Changes to the original paths after capture cannot change what is
consumed. Required consumer files must have checksums; unsafe paths and
unexpected ShareGPT loader references fail before consumption.

This establishes integrity relative to the supplied manifest, not authenticity
of its author, policy compliance, model quality or training effectiveness. The
production dataset verifier retains its existing semantic and policy checks.

## Evidence

- Ten added regression cases cover snapshot binding, tampering, missing hashes,
  unsafe paths and ShareGPT redirection.
- Full suite: 637 passed, 3 Linux ACL capability skips, 43 warnings; 93% coverage.
- Ruff, scoped formatting, mypy, pip check and wheel/sdist build passed.
- Actual locked TRL 1.14.0 standard/conversational and LLaMAFactory 0.9.5
  ShareGPT loading, templates, tokenizer and collator passed using one
  deterministic synthetic record. No training or pretrained model downloads.
- A changed `chosen` field with an unchanged manifest was rejected with a
  checksum mismatch before the real TRL loader started.

Remote CI must be checked on the final published commit. These local checks do
not represent real-user or live-provider acceptance.

## Practice reviewed

[Hugging Face Datasets checksum verification](https://github.com/huggingface/datasets/blob/main/src/datasets/utils/info_utils.py)
fails on missing, extra or mismatched checksums. Its default basic verification
is not itself proof of downloaded-byte integrity. The useful pattern is an
explicit integrity boundary before consumption. Datasets is
[Apache-2.0](https://github.com/huggingface/datasets/blob/main/LICENSE).
This change uses original standard-library code, with no copied implementation
or new runtime dependency.
