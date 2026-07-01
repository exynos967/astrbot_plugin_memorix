from astrbot_plugin_memorix.memorix.scope_router import ScopeRouter


class DummyEvent:
    def __init__(self, platform="aiocqhttp", sender="10001", group="", umo=""):
        self._platform = platform
        self._sender = sender
        self._group = group
        self.unified_msg_origin = umo or f"{platform}:group:{group or sender}"

    def get_platform_name(self):
        return self._platform

    def get_sender_id(self):
        return self._sender

    def get_group_id(self):
        return self._group


def test_platform_global_scope():
    router = ScopeRouter(mode="platform_global")
    assert router.resolve(DummyEvent(platform="telegram")) == "telegram"


def test_group_global_scope_fallback_user():
    router = ScopeRouter(mode="group_global")
    assert router.resolve(DummyEvent(platform="aiocqhttp", sender="u1", group="")) == "aiocqhttp:group:u1"


def test_cron_event_routes_to_original_group_scope():
    router = ScopeRouter(mode="group_global")
    event = DummyEvent(
        platform="cron",
        sender="123",
        group="",
        umo="aiocqhttp:GroupMessage:123",
    )

    assert router.resolve(event) == "aiocqhttp:group:123"
