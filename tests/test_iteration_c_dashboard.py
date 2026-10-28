"""Streamlit state tests complement, rather than replace, browser acceptance."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ecoalign_forge.config import settings
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.schemas.kernel import RunConfig, canonical
from ecoalign_forge.workbench.review import read_reviews, snapshot_run
from tests.test_iteration_b_kernel import RecordedTransport, kernel
from tests.test_iteration_b_policy import pack_data
from tests.test_iteration_c_workbench import demo


@pytest.mark.asyncio
async def test_dashboard_accept_abstain_dataset_and_error(tmp_path, monkeypatch):
    run, cases = await demo(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "datasets_dir", tmp_path / "datasets")
    app = AppTest.from_file(
        str(Path(__file__).parents[1] / "dashboard/app.py"), default_timeout=60
    ).run()
    assert not app.exception
    app.sidebar.selectbox[0].set_value("demo").run()
    assert not app.exception
    app.sidebar.radio[1].set_value("样本复核").run()
    next(w for w in app.selectbox if w.label == "选择样本").set_value(cases[0]["id"]).run()
    assert not app.exception
    next(w for w in app.text_input if w.label == "复核者名称").set_value("automated AppTest")
    next(w for w in app.text_area if w.label == "复核理由（必填）").set_value(
        "Offline UI engineering fixture"
    )
    next(w for w in app.button if w.label == "校验并保存复核").click().run()
    assert not app.exception and len(read_reviews(run)) == 1
    next(w for w in app.radio if w.label == "复核动作").set_value("弃权").run()
    next(w for w in app.text_input if w.label == "复核者名称").set_value("automated AppTest")
    next(w for w in app.text_area if w.label == "复核理由（必填）").set_value(
        "Insufficient source fixture"
    )
    next(w for w in app.button if w.label == "校验并保存复核").click().run()
    assert not app.exception and len(read_reviews(run)) == 2
    app.sidebar.radio[1].set_value("数据集").run()
    assert not app.exception
    next(w for w in app.checkbox if w.label == "包含未复核机器结果（仅候选预览）").check().run()
    next(w for w in app.button if w.label == "生成不可变数据集版本").click().run()
    assert not app.exception
    files = list((tmp_path / "datasets").glob("demo/curated/*/manifest.json"))
    assert len(files) == 1
    (files[0].parent / "pairs.jsonl").write_text("broken")
    app.run()
    assert not app.exception and any("校验失败" in e.value for e in app.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_field", ["start", "end", "both"])
async def test_correction_repairs_out_of_source_evidence_without_rewriting_candidate(
    tmp_path, invalid_field
):
    pack = PolicyPack.model_validate(pack_data())
    recorded = RecordedTransport(pack)

    async def invalid_judge(payload, attempt_id, config):
        result = await recorded(payload, attempt_id, config)
        if payload["stage"] == "judge":
            value = json.loads(result.content)
            fields = ("start", "end") if invalid_field == "both" else (invalid_field,)
            for field in fields:
                value["evidence"][0][field] = len(recorded.text) + 5 + (field == "end")
            result = result.model_copy(update={"content": canonical(value)})
        return result

    k = kernel(tmp_path, invalid_judge)
    await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    run = k.last_run_dir
    view = snapshot_run(run)["cases"][0]
    assert view["case"]["state"] == "excluded" and view["effective_final"] is None
    original = view["stages"]["judge"]
    machine_bytes = (run / "run.sqlite3").read_bytes()
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from dashboard.review_view import render_review\n"
        f"render_review(Path({str(run)!r}))\n",
        default_timeout=60,
    ).run()
    assert not app.exception
    next(w for w in app.radio if w.label == "复核动作").set_value("改判").run()
    assert not app.exception and any("原记录未改" in w.value for w in app.warning)
    assert not read_reviews(run)
    assert snapshot_run(run)["cases"][0]["stages"]["judge"] == original
    assert (run / "run.sqlite3").read_bytes() == machine_bytes
    start = next(w for w in app.number_input if w.label == "起始 CONTACT")
    end = next(w for w in app.number_input if w.label == "结束 CONTACT")
    assert 0 <= start.value <= len(recorded.text)
    assert 0 <= end.value <= len(recorded.text)
    start.set_value(0)
    end.set_value(len(recorded.text))
    next(w for w in app.text_input if w.label == "复核者名称").set_value("automated AppTest")
    next(w for w in app.text_area if w.label == "复核理由（必填）").set_value(
        "Controlled repair of the displayed Unicode source span, not human acceptance"
    )
    next(w for w in app.button if w.label == "校验并保存复核").click().run()
    assert not app.exception
    saved = read_reviews(run)
    assert len(saved) == 1 and saved[0]["decision"]["action"] == "correct"
    evidence = saved[0]["final"]["evaluation"]["evidence"][0]
    assert (evidence["start"], evidence["end"], evidence["quote"]) == (
        0,
        len(recorded.text),
        recorded.text,
    )
    assert snapshot_run(run)["cases"][0]["stages"]["judge"] == original
    assert (run / "run.sqlite3").read_bytes() == machine_bytes
