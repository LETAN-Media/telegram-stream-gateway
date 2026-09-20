"""Unit tests: R2 mirror key/dedup/settings. No network, no secrets."""
import asyncio

from app.r2_mirror import MirrorManager, object_key, r2_settings_from_env


def test_object_key_deterministic():
    assert object_key(-1001, 25) == "videos/-1001/25.mp4"
    assert object_key(-1001, 25) == object_key(-1001, 25)
    assert object_key(-1001, 25) != object_key(-1001, 26)


def test_settings_prefers_new_names():
    s = r2_settings_from_env({
        "R2_NEW_ENDPOINT": "https://new.example",
        "R2_NEW_ACCESS_KEY_ID": "newkey",
        "R2_NEW_SECRET_ACCESS_KEY": "newsecret",
        "R2_NEW_BUCKET": "newbucket",
        "R2_ENDPOINT": "https://old.example",
        "R2_ACCESS_KEY_ID": "oldkey",
        "R2_SECRET_ACCESS_KEY": "oldsecret",
        "R2_BUCKET": "oldbucket",
    })
    assert s.configured is True
    assert s.endpoint == "https://new.example"
    assert s.bucket == "newbucket"


def test_settings_falls_back_to_legacy_names():
    s = r2_settings_from_env({
        "R2_ENDPOINT": "https://e.example",
        "R2_ACCESS_KEY_ID": "k",
        "R2_SECRET_ACCESS_KEY": "s",
        "R2_BUCKET": "b",
    })
    assert s.configured is True
    assert s.bucket == "b"


def test_settings_missing_is_not_configured():
    assert r2_settings_from_env({}).configured is False


def test_trigger_disabled_without_config():
    m = MirrorManager(r2_settings_from_env({}))
    assert m.trigger(object(), -1001, 1, None) == "DISABLED"


def test_trigger_dedups_concurrent_requests():
    async def go():
        m = MirrorManager(r2_settings_from_env({
            "R2_ENDPOINT": "https://e.example", "R2_ACCESS_KEY_ID": "k",
            "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "b",
        }))

        async def slow_media(client, chat, mid):
            await asyncio.sleep(5)
            return None

        first = m.trigger(object(), -1001, 1, slow_media)
        second = m.trigger(object(), -1001, 1, slow_media)
        # cleanup background task registry
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task():
                task.cancel()
        return first, second

    first, second = asyncio.new_event_loop().run_until_complete(go())
    assert first == "STARTED"
    assert second == "ALREADY_RUNNING"
