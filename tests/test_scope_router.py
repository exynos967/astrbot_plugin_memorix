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
    assert router.resolve(DummyEvent(platform="aiocqhttp", sender="u1", group="")) == "aiocqhttp:user:u1"


def test_group_global_does_not_collide_private_user_with_group_id():
    router = ScopeRouter(mode="group_global")
    private = router.resolve(DummyEvent(platform="aiocqhttp", sender="123", group=""))
    group = router.resolve(DummyEvent(platform="aiocqhttp", sender="999", group="123"))
    assert private == "aiocqhttp:user:123"
    assert group == "aiocqhttp:group:123"
    assert private != group


def test_unknown_mode_falls_back_to_group_global():
    router = ScopeRouter(mode="not_a_real_mode")
    assert router.resolve(DummyEvent(platform="aiocqhttp", sender="u1", group="g1")) == "aiocqhttp:group:g1"


def test_cron_event_routes_to_original_group_scope():
    router = ScopeRouter(mode="group_global")
    event = DummyEvent(
        platform="cron",
        sender="123",
        group="",
        umo="aiocqhttp:GroupMessage:123",
    )

    assert router.resolve(event) == "aiocqhttp:group:123"


def test_cron_friend_message_routes_to_user_scope():
    router = ScopeRouter(mode="group_global")
    event = DummyEvent(
        platform="cron",
        sender="123",
        group="",
        umo="aiocqhttp:FriendMessage:123",
    )

    assert router.resolve(event) == "aiocqhttp:user:123"
