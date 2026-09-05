from astrbot_plugin_memorix.memorix.amemorix.settings import mask_sensitive


def test_mask_sensitive_hides_api_key_and_paths():
    masked = mask_sensitive(
        {
            "embedding": {"openapi": {"api_key": "sk-secret-key", "model": "text-emb"}},
            "storage": {"data_dir": "/home/user/plugin_data"},
            "web": {"import": {"path_aliases": {"docs": "/opt/docs"}}},
        }
    )

    assert masked["embedding"]["openapi"]["api_key"] != "sk-secret-key"
    assert "sk-secret-key" not in str(masked)
    assert masked["embedding"]["openapi"]["model"] == "text-emb"
    assert masked["storage"]["data_dir"] != "/home/user/plugin_data"
    assert "/opt/docs" not in str(masked["web"]["import"]["path_aliases"])
