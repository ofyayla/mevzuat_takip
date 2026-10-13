"""tools/shared.py: yol ayracından bağımsız dosya listesi (Windows'ta `sync` ortak dosyaları silmesin)."""
import shared


def test_files_returns_posix_paths_for_directories_and_single_files(tmp_path):
    (tmp_path / "app" / "monitoring").mkdir(parents=True)
    (tmp_path / "app" / "db.py").write_text("x")
    (tmp_path / "app" / "monitoring" / "reprocess.py").write_text("x")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "db.pyc").write_text("x")

    assert shared.files(tmp_path, "app") == {"app/db.py", "app/monitoring/reprocess.py"}
    assert shared.files(tmp_path, "app/db.py") == {"app/db.py"}   # tek dosya ile dizin aynı biçimde karşılaştırılır
    assert shared.files(tmp_path, "app/yok.py") == set()
