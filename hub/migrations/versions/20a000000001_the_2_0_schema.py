"""The schema of HomeMusic 2.0.

The one migration 2.0 starts from: made of the models (alembic autogenerate). A database of 1.x is not carried
over — 2.0 begins with an empty one. Later changes of the schema are migrations after this one.

Revision ID: 20a000000001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "20a000000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value_json', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('catalog_cache',
    sa.Column('key', sa.String(length=512), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.Column('ttl_s', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('email_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('email_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_codes_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_codes_email'), ['email'], unique=False)

    op.create_table('favorite_subjects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_type', sa.String(length=16), nullable=False),
    sa.Column('canonical_key', sa.String(length=128), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_id', sa.String(length=64), nullable=True),
    sa.Column('aliases', sa.JSON(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('enriched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('target_type', 'canonical_key', name='uq_favorite_subject_key')
    )
    with op.batch_alter_table('favorite_subjects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_favorite_subjects_nd_id'), ['nd_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_favorite_subjects_ref'), ['ref'], unique=False)

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(), nullable=False),
    sa.Column('locked_by', sa.String(length=64), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_pick', ['status', 'run_after'], unique=False)

    op.create_table('source_map',
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('match', sa.String(length=16), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('source', 'kind', 'external_id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nd_username', sa.String(length=128), nullable=False),
    sa.Column('nd_password_enc', sa.Text(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('google_sub', sa.String(length=255), nullable=True),
    sa.Column('google_email', sa.String(length=320), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    sa.Column('lb_token_enc', sa.Text(), nullable=True),
    sa.Column('lb_user', sa.String(length=128), nullable=True),
    sa.Column('lb_sent_until', sa.DateTime(), nullable=True),
    sa.Column('tg_bot_token_enc', sa.Text(), nullable=True),
    sa.Column('tg_bot_username', sa.String(length=64), nullable=True),
    sa.Column('tg_chat_id', sa.String(length=32), nullable=True),
    sa.Column('tg_chat_name', sa.String(length=128), nullable=True),
    sa.Column('tg_with_lyrics', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('google_sub'),
    sa.UniqueConstraint('nd_username')
    )
    op.create_table('ai_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_usage_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_usage_user_id'), ['user_id'], unique=False)

    op.create_table('albums',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('album_artist', sa.String(length=512), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('release_type', sa.String(length=32), nullable=True),
    sa.Column('deezer_album_id', sa.String(length=32), nullable=True),
    sa.Column('upc', sa.String(length=32), nullable=True),
    sa.Column('genre', sa.String(length=128), nullable=True),
    sa.Column('cover_path', sa.String(length=1024), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uid')
    )
    with op.batch_alter_table('albums', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_albums_deezer_album_id'), ['deezer_album_id'], unique=False)

    op.create_table('artist_release_checks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('artist_ref', sa.String(length=64), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'artist_ref', name='uq_artist_release_checks')
    )
    op.create_table('artist_releases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('artist_ref', sa.String(length=64), nullable=False),
    sa.Column('artist', sa.String(length=512), nullable=False),
    sa.Column('album_ref', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('record_type', sa.String(length=16), nullable=False),
    sa.Column('release_date', sa.Date(), nullable=False),
    sa.Column('cover_url', sa.String(length=512), nullable=True),
    sa.Column('nb_tracks', sa.Integer(), nullable=True),
    sa.Column('found_at', sa.DateTime(), nullable=False),
    sa.Column('seen_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'album_ref', name='uq_artist_releases')
    )
    with op.batch_alter_table('artist_releases', schema=None) as batch_op:
        batch_op.create_index('ix_artist_releases_user_date', ['user_id', 'release_date'], unique=False)

    op.create_table('assistant_chats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('messages', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assistant_chats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_chats_user_id'), ['user_id'], unique=False)

    op.create_table('assistant_daily_usage',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('messages', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'day')
    )
    op.create_table('discovery_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('week', sa.Date(), nullable=False),
    sa.Column('tracks_json', sa.JSON(), nullable=False),
    sa.Column('stats_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'week', name='uq_discovery_issues')
    )
    op.create_table('dislikes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('artist_ref', sa.String(length=64), nullable=True),
    sa.Column('artist', sa.String(length=512), nullable=True),
    sa.Column('title', sa.String(length=512), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('dislikes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dislikes_user_id'), ['user_id'], unique=False)

    op.create_table('download_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=True),
    sa.Column('target_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('download_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_download_batches_user_id'), ['user_id'], unique=False)

    op.create_table('imports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('account', sa.String(length=256), nullable=True),
    sa.Column('token_enc', sa.Text(), nullable=True),
    sa.Column('keep_token', sa.Boolean(), nullable=False),
    sa.Column('auth_json', sa.JSON(), nullable=False),
    sa.Column('options_json', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('imports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_imports_user_id'), ['user_id'], unique=False)

    op.create_table('invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=128), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('used_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['used_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('listen_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('ms', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('spotify_id', sa.String(length=32), nullable=True),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('artist', sa.String(length=512), nullable=False),
    sa.Column('album', sa.String(length=512), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'ts', 'key', name='uq_listen_history')
    )
    with op.batch_alter_table('listen_history', schema=None) as batch_op:
        batch_op.create_index('ix_listen_history_user_ts', ['user_id', 'ts'], unique=False)

    op.create_table('mix_settings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('mix_hour', sa.Integer(), nullable=False),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('artists_json', sa.JSON(), nullable=False),
    sa.Column('last_build_on', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('navidrome_sync',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('last_scrobble_id', sa.Integer(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.Column('favorites_synced_at', sa.DateTime(), nullable=True),
    sa.Column('favorites_bootstrapped_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('playlists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('mirrored', sa.JSON(), nullable=False),
    sa.Column('nd_cover', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('playlists', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_playlists_nd_playlist_id'), ['nd_playlist_id'], unique=False)
        batch_op.create_index('ix_playlists_user', ['user_id', 'kind'], unique=False)

    op.create_table('search_history',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('query_key', sa.String(length=1024), nullable=False),
    sa.Column('query', sa.String(length=512), nullable=False),
    sa.Column('searched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'query_key')
    )
    op.create_table('sessions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('smart_hidden',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('context', sa.String(length=80), nullable=False),
    sa.Column('fingerprint', sa.String(length=1024), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('artist', sa.String(length=256), server_default='', nullable=False),
    sa.Column('title', sa.String(length=256), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'context', 'fingerprint', name='uq_smart_hidden')
    )
    with op.batch_alter_table('smart_hidden', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_smart_hidden_user_id'), ['user_id'], unique=False)

    op.create_table('smart_sessions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('context', sa.String(length=80), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=True),
    sa.Column('own_count', sa.Integer(), nullable=False),
    sa.Column('per_rec', sa.Integer(), nullable=False),
    sa.Column('first_id', sa.String(length=80), nullable=True),
    sa.Column('taste_model', sa.String(length=8), nullable=True),
    sa.Column('origin', sa.String(length=32), nullable=True),
    sa.Column('client', sa.String(length=32), nullable=True),
    sa.Column('next_calls', sa.Integer(), server_default='0', nullable=False),
    sa.Column('state_json', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('smart_sessions', schema=None) as batch_op:
        batch_op.create_index('ix_smart_sessions_user_context', ['user_id', 'context', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_smart_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('tag_selections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('scope', sa.String(length=80), nullable=False),
    sa.Column('filter_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tag_selections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tag_selections_user_id'), ['user_id'], unique=False)

    op.create_table('tags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group', sa.String(length=8), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('code', sa.String(length=16), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.create_index('ix_tags_group_user', ['group', 'user_id'], unique=False)

    op.create_table('tg_chats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('photo', sa.LargeBinary(), nullable=True),
    sa.Column('photo_key', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'chat_id', name='uq_tg_chats_user_chat')
    )
    with op.batch_alter_table('tg_chats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tg_chats_user_id'), ['user_id'], unique=False)

    op.create_table('assistant_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('input_kind', sa.String(length=16), nullable=False),
    sa.Column('input_ref', sa.JSON(), nullable=False),
    sa.Column('extraction_json', sa.JSON(), nullable=True),
    sa.Column('resolution_json', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('confirm_token', sa.String(length=64), nullable=True),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('assistant_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_tasks_user_id'), ['user_id'], unique=False)

    op.create_table('import_collections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('selected', sa.Boolean(), nullable=False),
    sa.Column('cover_url', sa.String(length=1024), nullable=True),
    sa.Column('playlist_id', sa.Integer(), nullable=True),
    sa.Column('given', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_collections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_collections_import_id'), ['import_id'], unique=False)

    op.create_table('mixes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('nd_playlist_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('stats_json', sa.JSON(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mixes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mixes_user_id'), ['user_id'], unique=False)

    op.create_table('playlist_covers',
    sa.Column('playlist_id', sa.Integer(), nullable=False),
    sa.Column('image', sa.LargeBinary(), nullable=False),
    sa.Column('key', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('playlist_id')
    )
    op.create_table('smart_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('handed_at', sa.DateTime(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('slot', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=False),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('fingerprint', sa.String(length=1024), server_default='', nullable=False),
    sa.Column('artist', sa.String(length=256), server_default='', nullable=False),
    sa.Column('title', sa.String(length=256), server_default='', nullable=False),
    sa.Column('generator', sa.String(length=32), nullable=False),
    sa.Column('gens', sa.JSON(), nullable=True),
    sa.Column('familiar', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('score', sa.Float(), server_default='0', nullable=False),
    sa.Column('sim', sa.Float(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('state', sa.String(length=12), server_default='handed', nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['smart_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'ref', name='uq_smart_items_session_ref')
    )
    with op.batch_alter_table('smart_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_smart_items_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_smart_items_user_id'), ['user_id'], unique=False)

    op.create_table('tracks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('album_id', sa.Integer(), nullable=True),
    sa.Column('artist', sa.String(length=512), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('album', sa.String(length=512), nullable=True),
    sa.Column('album_artist', sa.String(length=512), nullable=True),
    sa.Column('track_no', sa.Integer(), nullable=True),
    sa.Column('disc_no', sa.Integer(), nullable=True),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('genre', sa.String(length=128), nullable=True),
    sa.Column('duration_s', sa.Integer(), nullable=True),
    sa.Column('isrc', sa.String(length=16), nullable=True),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('explicit', sa.Boolean(), nullable=True),
    sa.Column('fingerprint', sa.String(length=1024), nullable=False),
    sa.Column('catalog', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['album_id'], ['albums.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ref')
    )
    with op.batch_alter_table('tracks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tracks_album_id'), ['album_id'], unique=False)
        batch_op.create_index('ix_tracks_fingerprint', ['fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_tracks_isrc'), ['isrc'], unique=False)

    op.create_table('wave_sessions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('seed_json', sa.JSON(), nullable=True),
    sa.Column('batch_id', sa.Integer(), nullable=True),
    sa.Column('state_json', sa.JSON(), nullable=False),
    sa.Column('origin', sa.String(length=32), nullable=True),
    sa.Column('client', sa.String(length=32), nullable=True),
    sa.Column('restart_of', sa.String(length=32), nullable=True),
    sa.Column('taste_model', sa.String(length=8), nullable=True),
    sa.Column('request_json', sa.JSON(), nullable=True),
    sa.Column('next_calls', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cleared_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('wave_sessions', schema=None) as batch_op:
        batch_op.create_index('ix_wave_sessions_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_wave_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('download_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('query_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('chosen_candidate_json', sa.JSON(), nullable=True),
    sa.Column('candidates_json', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('warnings', sa.JSON(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('force', sa.Boolean(), nullable=False),
    sa.Column('hidden', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['download_batches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('download_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_download_items_batch_id'), ['batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_download_items_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_download_items_user_id'), ['user_id'], unique=False)

    op.create_table('lyrics',
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=True),
    sa.Column('lines_json', sa.JSON(), nullable=True),
    sa.Column('plain', sa.Text(), nullable=True),
    sa.Column('search_text', sa.Text(), nullable=True),
    sa.Column('offset_ms', sa.Integer(), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('track_id')
    )
    with op.batch_alter_table('lyrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lyrics_kind'), ['kind'], unique=False)

    op.create_table('lyrics_translations',
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('lang', sa.String(length=8), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=True),
    sa.Column('lines_json', sa.JSON(), nullable=True),
    sa.Column('source_sig', sa.String(length=40), nullable=True),
    sa.Column('edited_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('track_id', 'lang')
    )
    op.create_table('play_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('target_type', sa.String(length=16), server_default='track', nullable=False),
    sa.Column('nd_album_id', sa.String(length=64), nullable=True),
    sa.Column('nd_artist_id', sa.String(length=64), nullable=True),
    sa.Column('target_data', sa.JSON(), nullable=True),
    sa.Column('reverts_event_id', sa.String(length=64), nullable=True),
    sa.Column('mirror_pending', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('playback_id', sa.String(length=64), nullable=True),
    sa.Column('device_id', sa.String(length=64), nullable=True),
    sa.Column('listened_ms', sa.Integer(), nullable=True),
    sa.Column('track_metadata', sa.JSON(), nullable=True),
    sa.Column('superseded_scrobble', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('position_s', sa.Float(), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('context', sa.String(length=128), nullable=True),
    sa.Column('client', sa.String(length=32), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('launch_id', sa.String(length=64), nullable=True),
    sa.Column('launch_origin', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'event_id', name='uq_play_events_user_event')
    )
    with op.batch_alter_table('play_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_play_events_nd_album_id'), ['nd_album_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_play_events_nd_artist_id'), ['nd_artist_id'], unique=False)
        batch_op.create_index('ix_play_events_playback', ['user_id', 'device_id', 'playback_id'], unique=False)
        batch_op.create_index('ix_play_events_song', ['nd_song_id'], unique=False)
        batch_op.create_index('ix_play_events_track', ['track_id'], unique=False)
        batch_op.create_index('ix_play_events_user_launch', ['user_id', 'launch_id'], unique=False)
        batch_op.create_index('ix_play_events_user_target_ts', ['user_id', 'target_type', 'ts', 'id'], unique=False)
        batch_op.create_index('ix_play_events_user_ts', ['user_id', 'ts'], unique=False)

    op.create_table('playlist_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('playlist_id', sa.Integer(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('playlist_items', schema=None) as batch_op:
        batch_op.create_index('ix_playlist_items_order', ['playlist_id', 'position'], unique=False)
        batch_op.create_index(batch_op.f('ix_playlist_items_track_id'), ['track_id'], unique=False)

    op.create_table('tags_removed',
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('track_id', 'tag_id')
    )
    op.create_table('track_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('uid', sa.String(length=36), nullable=False),
    sa.Column('path', sa.String(length=1024), nullable=False),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('bitrate_kbps', sa.Integer(), nullable=True),
    sa.Column('codec', sa.String(length=16), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=True),
    sa.Column('source_url', sa.String(length=2048), nullable=True),
    sa.Column('source_upload_date', sa.Date(), nullable=True),
    sa.Column('acquired_at', sa.DateTime(), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('warnings', sa.JSON(), nullable=False),
    sa.Column('added_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('path'),
    sa.UniqueConstraint('uid')
    )
    with op.batch_alter_table('track_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_track_files_nd_song_id'), ['nd_song_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_track_files_track_id'), ['track_id'], unique=False)

    op.create_table('track_links',
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('service', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('track_id', 'service')
    )
    with op.batch_alter_table('track_links', schema=None) as batch_op:
        batch_op.create_index('ix_track_links_external', ['service', 'external_id'], unique=False)

    op.create_table('track_tags',
    sa.Column('track_id', sa.Integer(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.Column('auto', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('track_id', 'tag_id')
    )
    with op.batch_alter_table('track_tags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_track_tags_tag_id'), ['tag_id'], unique=False)

    op.create_table('uploads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('staging_path', sa.String(length=1024), nullable=False),
    sa.Column('orig_name', sa.String(length=512), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('probed_tags_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_uploads_user_id'), ['user_id'], unique=False)

    op.create_table('wave_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('first_handed_at', sa.DateTime(), nullable=False),
    sa.Column('last_handed_at', sa.DateTime(), nullable=False),
    sa.Column('handed_count', sa.Integer(), server_default='1', nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=False),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('artist', sa.String(length=256), server_default='', nullable=False),
    sa.Column('title', sa.String(length=256), server_default='', nullable=False),
    sa.Column('generator', sa.String(length=32), nullable=False),
    sa.Column('gens', sa.JSON(), nullable=True),
    sa.Column('familiar', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('score', sa.Float(), server_default='0', nullable=False),
    sa.Column('sim', sa.Float(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['wave_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'ref', name='uq_wave_items_session_ref')
    )
    with op.batch_alter_table('wave_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_wave_items_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_wave_items_user_id'), ['user_id'], unique=False)

    op.create_table('import_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.Column('collection_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('raw_json', sa.JSON(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('match', sa.String(length=16), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('download_item_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['collection_id'], ['import_collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['download_item_id'], ['download_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_items_collection_id'), ['collection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_download_item_id'), ['download_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_import_id'), ['import_id'], unique=False)
        batch_op.create_index('ix_import_items_ref', ['ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_items_status'), ['status'], unique=False)

    op.create_table('mix_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mix_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('familiar', sa.Boolean(), nullable=False),
    sa.Column('ref', sa.String(length=64), nullable=True),
    sa.Column('nd_song_id', sa.String(length=64), nullable=True),
    sa.Column('track_id', sa.Integer(), nullable=True),
    sa.Column('generator', sa.String(length=32), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('features_json', sa.JSON(), nullable=False),
    sa.Column('downloaded_for_mix', sa.Boolean(), nullable=False),
    sa.Column('download_item_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['download_item_id'], ['download_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['mix_id'], ['mixes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mix_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mix_items_download_item_id'), ['download_item_id'], unique=False)
        batch_op.create_index('ix_mix_items_mix', ['mix_id', 'state', 'position'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_nd_song_id'), ['nd_song_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_ref'), ['ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_mix_items_track_id'), ['track_id'], unique=False)


def downgrade() -> None:
    raise NotImplementedError("there is nothing before 2.0")
