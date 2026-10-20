import pytest

from bridge_dealer import dds_native


def test_checkout_library_is_found_without_external_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIDGE_DDS_LIBRARY", raising=False)
    monkeypatch.setattr(dds_native, "__file__", str(tmp_path / "src/bridge_dealer/dds_native.py"))
    library = tmp_path / "lib/libdds.so"
    library.parent.mkdir()
    library.touch()
    assert dds_native.library_path() == library


def test_explicit_library_overrides_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_DDS_LIBRARY", str(tmp_path / "environment.so"))
    assert dds_native.library_path(tmp_path / "explicit.so") == tmp_path / "explicit.so"
    assert dds_native.library_path() == tmp_path / "environment.so"


def test_missing_library_explains_how_to_build_it(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIDGE_DDS_LIBRARY", raising=False)
    monkeypatch.setattr(dds_native, "__file__", str(tmp_path / "src/bridge_dealer/dds_native.py"))
    with pytest.raises(FileNotFoundError, match="scripts/build_dds.py"):
        dds_native.library_path()
