"""Registration, login, refresh, and the /players/me round-trip."""


REGISTER_PAYLOAD = {
    "email": "player@example.com",
    "password": "verystrongpass",
    "display_name": "Example Player",
    "preferred_color": "white",
}


async def test_register_then_login_then_me(client):
    r = await client.post("/auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == REGISTER_PAYLOAD["email"]
    assert body["display_name"] == REGISTER_PAYLOAD["display_name"]
    assert body["preferred_color"] == "white"
    assert body["role"] == "player"
    assert body["settings_json"] == {}

    r = await client.post(
        "/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["token_type"] == "bearer"
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]
    assert access and refresh and access != refresh

    r = await client.get("/players/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["email"] == REGISTER_PAYLOAD["email"]

    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    new_tokens = r.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"]


async def test_duplicate_email_rejected(client):
    r = await client.post(
        "/auth/register",
        json={**REGISTER_PAYLOAD, "email": "dup@example.com"},
    )
    assert r.status_code == 201
    r = await client.post(
        "/auth/register",
        json={**REGISTER_PAYLOAD, "email": "DUP@example.com"},  # citext — same email
    )
    assert r.status_code == 409


async def test_login_wrong_password(client):
    await client.post(
        "/auth/register",
        json={**REGISTER_PAYLOAD, "email": "wrongpw@example.com"},
    )
    r = await client.post(
        "/auth/login",
        json={"email": "wrongpw@example.com", "password": "nope-nope-nope"},
    )
    assert r.status_code == 401


async def test_me_requires_bearer(client):
    r = await client.get("/players/me")
    assert r.status_code == 401
    r = await client.get("/players/me", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


async def test_patch_me_merges_settings(client):
    r = await client.post(
        "/auth/register",
        json={**REGISTER_PAYLOAD, "email": "patch@example.com"},
    )
    assert r.status_code == 201
    r = await client.post(
        "/auth/login",
        json={"email": "patch@example.com", "password": REGISTER_PAYLOAD["password"]},
    )
    access = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {access}"}

    # First patch sets two keys.
    r = await client.patch(
        "/players/me",
        headers=hdr,
        json={
            "rating_fide": 1800,
            "settings_json": {
                "blindfold_free_reveals_per_session": 3,
                "tts_voice": {"name": "Alex", "lang": "en-US"},
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rating_fide"] == 1800
    assert body["settings_json"]["blindfold_free_reveals_per_session"] == 3
    assert body["settings_json"]["tts_voice"] == {"name": "Alex", "lang": "en-US"}

    # Second patch adds a new key — the previous ones must survive.
    r = await client.patch(
        "/players/me",
        headers=hdr,
        json={"settings_json": {"blindfold_reveal_penalty": 0.2}},
    )
    assert r.status_code == 200
    settings = r.json()["settings_json"]
    assert settings["blindfold_free_reveals_per_session"] == 3
    assert settings["tts_voice"] == {"name": "Alex", "lang": "en-US"}
    assert settings["blindfold_reveal_penalty"] == 0.2


async def test_rating_bounds_rejected(client):
    r = await client.post(
        "/auth/register",
        json={**REGISTER_PAYLOAD, "email": "bounds@example.com"},
    )
    access = (await client.post(
        "/auth/login",
        json={"email": "bounds@example.com", "password": REGISTER_PAYLOAD["password"]},
    )).json()["access_token"]
    r = await client.patch(
        "/players/me",
        headers={"Authorization": f"Bearer {access}"},
        json={"rating_fide": 9999},
    )
    assert r.status_code == 422


async def test_patch_me_without_settings_leaves_them_untouched(client):
    await client.post("/auth/register", json={**REGISTER_PAYLOAD, "email": "keep@example.com"})
    r = await client.post(
        "/auth/login",
        json={"email": "keep@example.com", "password": REGISTER_PAYLOAD["password"]},
    )
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}

    voice = {"name": "Alex", "lang": "en-US"}
    r = await client.patch(
        "/players/me", headers=hdr, json={"settings_json": {"tts_voice": voice, "other": 1}}
    )
    assert r.status_code == 200

    # A profile-only patch must not clear settings_json.
    r = await client.patch("/players/me", headers=hdr, json={"display_name": "Renamed"})
    assert r.json()["settings_json"] == {"tts_voice": voice, "other": 1}

    # Saving only the voice (what the UI does) overwrites that key, not the rest.
    new_voice = {"name": "Daniel", "lang": "en-GB"}
    r = await client.patch("/players/me", headers=hdr, json={"settings_json": {"tts_voice": new_voice}})
    assert r.json()["settings_json"] == {"tts_voice": new_voice, "other": 1}

    # Persisted, not just echoed.
    r = await client.get("/players/me", headers=hdr)
    assert r.json()["settings_json"] == {"tts_voice": new_voice, "other": 1}
