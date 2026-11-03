"""Real pinned consumer preprocessing; local byte tokenizer, no models or training."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["DISABLE_VERSION_CHECK"] = "0"


@contextmanager
def verified_snapshot(path, consumer):
    """Bind consumer input and report to the same verified bytes, not live paths.

    This checks transport integrity against the supplied manifest, not authorship
    or policy correctness (the producer's verify_dataset owns those checks).
    """
    path = Path(path)
    manifest_bytes = (path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    files = manifest.get("files")
    required = (
        {"trl_standard.jsonl", "trl_conversational.jsonl"}
        if consumer == "trl"
        else {"dataset_info.json", "train_sharegpt.json"}
    )
    if not isinstance(files, dict) or not required.issubset(files):
        raise ValueError("Consumer files missing from dataset manifest")
    with TemporaryDirectory(prefix="preference-consumer-") as temporary:
        snapshot = Path(temporary)
        for name, expected in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or name == "manifest.json":
                raise ValueError("Unsafe manifest file path")
            source = path / relative
            if source.is_symlink() or not source.resolve().is_relative_to(path.resolve()):
                raise ValueError("Unsafe manifest file path")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Dataset checksum mismatch: " + name)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if consumer == "llamafactory":
            info = json.loads((snapshot / "dataset_info.json").read_bytes())
            entry = info.get("ecoalign_forge_dpo", {})
            if entry.get("file_name") != "train_sharegpt.json":
                raise ValueError("Unexpected ShareGPT consumer file")
        (snapshot / "manifest.json").write_bytes(manifest_bytes)
        yield snapshot, manifest, hashlib.sha256(manifest_bytes).hexdigest()


def tokenizer():
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    tokens = ["<pad>", "<eos>", "<unk>", *sorted(pre_tokenizers.ByteLevel.alphabet())]
    backend = Tokenizer(
        models.BPE(vocab={t: i for i, t in enumerate(tokens)}, merges=[], unk_token="<unk>")
    )
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    backend.decoder = decoders.ByteLevel()
    result = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        pad_token="<pad>",
        eos_token="<eos>",
        unk_token="<unk>",
        clean_up_tokenization_spaces=False,
    )
    result.chat_template = "{% for message in messages %}{{message['role'] + ':\n' + message['content'] + '\n'}}{% endfor %}{% if add_generation_prompt %}{{ 'assistant:\n' }}{% endif %}"
    return result


def verify_trl(path, tok):
    from datasets import load_dataset
    from trl import DPOConfig, DPOTrainer
    from trl.trainer.dpo_trainer import DataCollatorForPreference

    assert importlib.metadata.version("trl") == "1.14.0"
    results = []
    for name in ["trl_standard.jsonl", "trl_conversational.jsonl"]:
        data = load_dataset("json", data_files=str(path / name), split="train")
        args = DPOConfig(
            output_dir=str(path / "unused-training-output"),
            use_cpu=True,
            bf16=False,
            report_to="none",
            max_length=None,
        )
        processed = DPOTrainer._prepare_dataset(
            SimpleNamespace(_tokenizer=tok), data, tok, args, "acceptance"
        )
        rows = list(processed)
        assert len(rows) == len(data) > 0
        for original, row in zip(data, rows, strict=True):
            for key in ["chosen", "rejected"]:
                text = (
                    original[key][0]["content"]
                    if isinstance(original[key], list)
                    else original[key]
                )
                assert text in tok.decode(row[key + "_ids"])
                assert (
                    row["prompt_ids"]
                    == tok(original["prompt"], add_special_tokens=False)["input_ids"]
                    if isinstance(original["prompt"], str)
                    else row["prompt_ids"]
                    == tok.apply_chat_template(
                        original["prompt"], add_generation_prompt=True, return_dict=False
                    )
                )
        batch = DataCollatorForPreference(pad_token_id=tok.pad_token_id)(rows)
        for offset, key in [(0, "chosen"), (len(rows), "rejected")]:
            for i, row in enumerate(rows):
                real = batch["input_ids"][offset + i][
                    batch["attention_mask"][offset + i].bool()
                ].tolist()
                assert real == row["prompt_ids"] + row[key + "_ids"]
                completion = batch["input_ids"][offset + i][
                    batch["completion_mask"][offset + i].bool()
                ].tolist()
                assert completion == row[key + "_ids"]
        results.append(
            dict(
                format=name,
                rows=len(rows),
                tensor_shape=list(batch["input_ids"].shape),
                prompt_once=True,
                order_preserved=True,
            )
        )
    return results


def verify_llama(path, tok):
    from datasets import load_dataset
    from llamafactory.data.collator import PairwiseDataCollatorWithPadding
    from llamafactory.data.converter import SharegptDatasetConverter
    from llamafactory.data.parser import get_dataset_list
    from llamafactory.data.processor.pairwise import PairwiseDatasetProcessor
    from llamafactory.data.template import get_template_and_fix_tokenizer
    from llamafactory.hparams import DataArguments

    assert importlib.metadata.version("llamafactory") == "0.9.5"
    attrs = get_dataset_list(["ecoalign_forge_dpo"], str(path))[0]
    if attrs.load_from != "file" or attrs.dataset_name != "train_sharegpt.json":
        raise ValueError("Consumer must load the verified local ShareGPT file")
    args = DataArguments(template="default", dataset_dir=str(path), cutoff_len=200000)
    template = get_template_and_fix_tokenizer(tok, args)
    data = load_dataset("json", data_files=str(path / attrs.dataset_name), split="train")
    aligned = data.map(SharegptDatasetConverter(attrs, args), remove_columns=data.column_names)
    processor = PairwiseDatasetProcessor(template, tok, None, args)
    processed = aligned.map(
        processor.preprocess_dataset, batched=True, remove_columns=aligned.column_names
    )
    rows = list(processed)
    assert len(rows) == len(data) > 0
    for original, aligned_row, row in zip(data, aligned, rows, strict=True):
        for key in ["chosen", "rejected"]:
            actual = [t for t in row[key + "_labels"] if t != -100]
            assert original[key]["value"] in tok.decode(actual)
        prefix = [
            t
            for t, label in zip(row["chosen_input_ids"], row["chosen_labels"], strict=True)
            if label == -100
        ]
        expected, _ = template.encode_oneturn(
            tok,
            aligned_row["_prompt"] + [aligned_row["_response"][0]],
            aligned_row["_system"],
            aligned_row["_tools"],
        )
        assert prefix == expected
    batch = PairwiseDataCollatorWithPadding(tokenizer=tok, template=template)(rows)
    for offset, key in [(0, "chosen"), (len(rows), "rejected")]:
        for i, row in enumerate(rows):
            mask = batch["attention_mask"][offset + i].bool()
            assert batch["input_ids"][offset + i][mask].tolist() == row[key + "_input_ids"]
            assert batch["labels"][offset + i][mask].tolist() == row[key + "_labels"]
    return [
        dict(
            format="sharegpt",
            rows=len(rows),
            tensor_shape=list(batch["input_ids"].shape),
            prompt_once=True,
            order_preserved=True,
        )
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("consumer", choices=["trl", "llamafactory"])
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # A hard socket guard: dependency processing must remain entirely local.
    import socket

    def offline(*_args, **_kwargs):
        raise RuntimeError("Network disabled during consumer acceptance")

    socket.socket.connect = offline
    with verified_snapshot(args.dataset, args.consumer) as (snapshot, manifest, manifest_hash):
        tok = tokenizer()
        result = (
            verify_trl(snapshot, tok) if args.consumer == "trl" else verify_llama(snapshot, tok)
        )
    report = dict(
        consumer=args.consumer,
        version=importlib.metadata.version(args.consumer),
        tests=result,
        dataset_manifest=manifest,
        manifest_sha256=manifest_hash,
        input_files_verified=True,
        training_performed=False,
        pretrained_model_downloaded=False,
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(consumer=args.consumer, passed=True, tests=result), ensure_ascii=False))


if __name__ == "__main__":
    main()
