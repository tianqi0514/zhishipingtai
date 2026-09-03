from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[2] / "apps/api/static/app.js").read_text(
    encoding="utf-8"
)


def test_each_modal_uses_a_fresh_form_without_stale_feature_listeners() -> None:
    """A reused form leaked governance input listeners into later dialogs."""
    modal_start = APP_JS.index("async function modal(")
    modal_end = APP_JS.index("async function refreshLookups", modal_start)
    modal_source = APP_JS[modal_start:modal_end]

    assert "previousForm.cloneNode(true)" in modal_source
    assert "previousForm.replaceWith(form)" in modal_source
    assert "form.querySelector('#modal-submit')" in modal_source


def test_modal_buttons_are_bound_to_the_fresh_form() -> None:
    modal_start = APP_JS.index("async function modal(")
    modal_end = APP_JS.index("async function refreshLookups", modal_start)
    modal_source = APP_JS[modal_start:modal_end]

    assert "form.querySelector('#modal-close')" in modal_source
    assert "form.querySelector('#modal-cancel')" in modal_source
    assert "submitButton.disabled=false" in modal_source
