

async def test_a_tag_of_the_same_name_in_cyrillic_is_one_tag(hub_env):
    """SQLite's lower() leaves Cyrillic as it is: «Пляжный» made twice used to be two tags."""
    from app import db, tags
    from app.models import User
    from app.security import encrypt

    db.init_engine()
    try:
        async with db.session_scope() as s:
            user = User(nd_username="tagger", nd_password_enc=encrypt("x"))
            s.add(user)
            await s.flush()
            a = await tags.get_or_create(s, "own", "Пляжный", user_id=user.id)
            b = await tags.get_or_create(s, "own", "  пляжный ", user_id=user.id)
            c = await tags.get_or_create(s, "own", "ПЛЯЖНЫЙ", user_id=user.id)
            assert a.id == b.id == c.id
            other = await tags.get_or_create(s, "own", "Вечерний", user_id=user.id)
            try:
                await tags.rename(s, user.id, other.id, "пляжный")
            except ValueError:
                pass
            else:
                raise AssertionError("renamed into a name that is taken")
    finally:
        await db.dispose()
