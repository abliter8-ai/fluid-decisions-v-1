import json
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.download_models import config


def test_model_revisions_are_immutable():
    data = config(Path(__file__).parents[1] / "config" / "models.yaml")
    assert all(len(v["revision"]) == 40 for v in data["models"].values())


def test_downloader_resolves_revision_and_only_downloads(tmp_path, monkeypatch):
    from scripts.download_models import main
    calls = []
    resolved = 'a' * 40
    def download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs['local_dir'])
        target.mkdir(parents=True)
        # Importing downloaded code would fail this test.
        (target / 'modeling_d1.py').write_text('raise RuntimeError("must not import")')
        return str(target)
    class API:
        def model_info(self, repo_id, revision):
            return SimpleNamespace(sha=resolved)
    monkeypatch.setitem(sys.modules, 'huggingface_hub',
                        SimpleNamespace(snapshot_download=download, HfApi=API))
    manifest = tmp_path / 'manifest.json'
    assert main(['both', '--revision', 'main', '--local-dir', str(tmp_path / 'models'),
                 '--manifest', str(manifest)]) == 0
    records = json.loads(manifest.read_text())['models']
    assert len(calls) == len(records) == 2
    assert all(c['revision'] == resolved for c in calls)
    assert calls[0]['local_dir'] != calls[1]['local_dir']
    for record in records:
        assert json.loads((Path(record['snapshot']) / '.fluid-model.json').read_text())['revision'] == resolved
