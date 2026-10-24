"""Streamlit state tests complement, rather than replace, browser acceptance."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ecoalign_forge.config import settings
from ecoalign_forge.workbench.review import read_reviews
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
