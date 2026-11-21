// Mirrors hub/app/api/schemas.py, hub/app/catalog/models.py and hub/app/providers/base.py.
// Dates arrive as ISO strings ("YYYY-MM-DD" for date, ISO 8601 for datetime).

export type ISODate = string;
export type ISODateTime = string;

// --- auth ---------------------------------------------------------------------------------

export interface LoginIn {
  username: string;
  password: string;
}

export interface MeOut {
  id: number;
  username: string;
  is_admin: boolean;
  public_music_url: string;
  public_hub_url?: string;
  google_email?: string | null;
  email?: string | null;
  /** the server has a Last.fm key: «Карта» of artists is there */
  artist_map?: boolean;
}

export interface RegisterIn {
  code: string;
  username: string;
  password: string;
}

export interface AuthOptionsOut {
  invites: boolean;
  email_signup?: boolean;
  google_client_id: string | null;
  music_url?: string;
}

export interface EmailCodeOut {
  resend_in: number;
}

export interface EmailRegisterIn extends RegisterIn {
  email: string;
}

// --- admin ---------------------------------------------------------------------------------

export interface InviteIn {
  note: string;
  days?: number | null;
}

export interface InviteOut {
  id: number;
  code: string;
  note: string;
  status: 'active' | 'used' | 'expired';
  created_at: string;
  expires_at: string;
  used_at: string | null;
  used_by: string | null;
}

export interface AdminUserOut {
  id: number | null;
  username: string;
  name: string;
  is_admin: boolean;
  in_hub: boolean;
  created_at: string | null;
  last_seen_at: string | null;
  player_seen_at: string | null;
  google_email: string | null;
  email?: string | null;
  invite_note: string | null;
  ai_cost_usd: number;
  ai_cost_30d_usd: number;
  ai_requests: number;
  ai_last_at: string | null;
  downloads_done: number;
  downloads_failed: number;
  downloads_active: number;
  tracks_added: number;
  uploads: number;
  starred_songs: number | null;
  playlists: number | null;
}

export interface AdminUsersOut {
  users: AdminUserOut[];
  ai_cost_usd: number;
  ai_cost_30d_usd: number;
  library_tracks: number;
}

export interface AdminUserDetailOut extends AdminUserOut {
  ai_months: Array<{ month: string; cost_usd: number; requests: number }>;
  ai_recent: Array<{ kind: string; tokens_in: number; tokens_out: number; cost_usd: number; created_at: string }>;
  recent_downloads: Array<{ artist: string; title: string; status: string; created_at: string }>;
}

export interface OkOut {
  ok: boolean;
}

// --- providers ------------------------------------------------------------------------------

export interface Candidate {
  provider: string;
  provider_id: string;
  artist: string;
  title: string;
  duration_s?: number | null;
  bitrate_kbps?: number | null;
  explicit?: boolean | null;
  upload_date?: ISODate | null;
  page_url: string;
  download_url?: string | null;
  cover_url?: string | null;
  album?: string | null;
  year?: number | null;
  score?: number;
  enriched?: boolean;
  extra?: Record<string, unknown>;
}

// --- catalog ----------------------------------------------------------------------------------

/** `nd`: a library-only song; `file`: an exact search file used as a wave seed. */
export type CatalogSource = 'dz' | 'ym' | 'hm' | 'mb' | 'nd' | 'file';
export type RecordType = 'album' | 'single' | 'ep' | 'compile';
export type OwnedStatus = 'full' | 'partial' | 'none';

export interface CatalogArtist {
  ref: string;
  source: CatalogSource;
  id: string;
  name: string;
  picture_url?: string | null;
  /** for lists (Deezer 250 px); the big one only on the artist's own screen */
  picture_small_url?: string | null;
  nb_albums?: number | null;
  nb_fans?: number | null;
}

export interface CatalogTrack {
  ref: string;
  source: CatalogSource;
  id: string;
  title: string;
  artist: string;
  artists?: string[];
  /** the catalog refs of `artists`, in their order (null: unknown) */
  artist_refs?: (string | null)[];
  artist_ref?: string | null;
  album_ref?: string | null;
  album_title?: string | null;
  album_artist?: string | null;
  duration_s?: number | null;
  track_no?: number | null;
  disc_no?: number | null;
  isrc?: string | null;
  explicit?: boolean | null;
  release_date?: ISODate | null;
  cover_url?: string | null;
  /** for lists (Deezer 250 px): the big `cover_url` only on a screen of the track or album itself */
  cover_small_url?: string | null;
  preview_url?: string | null;
  /** how much the track is listened to, as its catalog counts it (Deezer); none: the catalog does not say */
  rank?: number | null;
  /** among an artist's most listened: how many listened to it on Last.fm */
  lastfm_listeners?: number | null;
}

export interface CatalogAlbum {
  ref: string;
  source: CatalogSource;
  id: string;
  title: string;
  artist: string;
  artist_ref?: string | null;
  /** everyone the release is credited to, the main one first, and their refs (an album's own page only) */
  artists?: string[];
  artist_refs?: (string | null)[];
  record_type: RecordType;
  release_date?: ISODate | null;
  year?: number | null;
  cover_url?: string | null;
  cover_small_url?: string | null;
  nb_tracks?: number | null;
  explicit?: boolean | null;
  upc?: string | null;
  genre?: string | null;
}

/** The file a track plays from: where it came from (a provider; `upload`), what it is (mp3, flac, opus…), its bitrate,
 * and what it sounds like when that is less than it says. */
export interface FileOut {
  source?: string | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  sounds_like_kbps?: number | null;
  /** the download that brought it kept its journal (`/file/<track>`) */
  journal?: boolean;
}

/** How a track's file came to the library: the journal of the download that brought it. */
export interface FileJournalOut {
  track_id: number;
  artist: string;
  title: string;
  file?: FileOut | null;
  steps: DownloadStep[];
  /** the download itself, when it is one's own */
  download_id?: number | null;
}

export interface TrackOut extends CatalogTrack {
  owned: boolean;
  /** how many recordings the song it is has here, itself among them (0: it is nobody's version) */
  versions?: number;
  /** an owned track: its file */
  file?: FileOut | null;
  library_track_id?: number | null;
  nd_song_id?: string | null;
  /** a download of it is under way (not counting ones queued only for ♥) */
  downloading: boolean;
  /** known by its name alone: no file and no record of the catalog — nothing to play until it is given a file */
  unknown?: boolean;
  /** the user ♥ it and a download that stars it is under way: the ♥ shows filled */
  /** in a list: when the track was added to it; else when it came into the library */
  added_at?: string | null;
}

export interface AlbumOut extends CatalogAlbum {
  owned_status: OwnedStatus;
  owned_count: number;
  tracks?: TrackOut[] | null;
  /** where the release can be opened: its own catalog, and another that has the same release (its own page only) */
  links?: { service: string; url: string }[];
}

export interface ArtistPageOut {
  artist: CatalogArtist;
  top_tracks: TrackOut[];
  albums: AlbumOut[];
  singles: AlbumOut[];
  compilations: AlbumOut[];
  /** releases said to be a namesake's: not among these */
  excluded?: number;
  /** how many listened to the artist on Last.fm (not known: none) */
  lastfm_listeners?: number | null;
  /** the wide picture over the page, kept on the Hub (none: the round photo is the page's head) */
  banner_url?: string | null;
  /** how many of the last `top_tracks` are Last.fm's, added where the catalog's own list is short */
  top_from_lastfm?: number;
  /** the catalog's records the page is drawn of — one artist may have several; the first is the page's own address */
  profiles?: string[];
  /** each of them, and Last.fm, as they show the artist */
  sources?: ArtistSourceOut[];
}

/** A place an artist is known in, as that place shows them: a record of a catalog, or Last.fm. */
export interface ArtistSourceOut {
  service: 'deezer' | 'catalog' | 'lastfm' | string;
  ref?: string | null;
  name: string;
  picture_url?: string | null;
  followers?: number | null;
  listeners?: number | null;
  releases?: number | null;
  url?: string | null;
}

/** A release the catalog files under an artist that is a namesake's — said so by someone here, for everyone. */
export interface NamesakeOut {
  artist_ref: string;
  artist: string;
  album_ref: string;
  title: string;
  record_type?: string | null;
  year?: number | null;
  cover_url?: string | null;
}

// --- library ----------------------------------------------------------------------------------

export type ReleaseType = 'album' | 'single' | 'ep' | 'compilation';

export interface LibTrackOut {
  id: number;
  nd_song_id: string | null;
  album_id: number | null;
  artist: string;
  title: string;
  album: string | null;
  album_artist: string | null;
  track_no: number | null;
  disc_no: number | null;
  year: number | null;
  duration_s: number | null;
  bitrate_kbps: number | null;
  codec: string | null;
  explicit: boolean | null;
  source: string | null;
  locked: boolean;
  warnings: unknown[];
  path: string;
  /** a search found it by these — its other names, its artists' other spellings — not by what the row shows */
  found_by?: string[];
  /** how many recordings the song it is has here (0: it is nobody's version) */
  versions?: number;
}

export interface LibAlbumOut {
  id: number;
  uid: string;
  title: string;
  album_artist: string;
  year: number | null;
  release_type: string | null;
  genre: string | null;
  deezer_album_id: string | null;
  /** the tracks of it the listener sees: their own (an admin: all) */
  track_count: number;
  has_cover: boolean;
  /** the album itself can be changed by the listener: all of its tracks are theirs */
  can_edit: boolean;
  nd_album_id?: string | null;
  tracks?: LibTrackOut[] | null;
}

export interface AlbumPatchIn {
  title?: string | null;
  album_artist?: string | null;
  year?: number | null;
  genre?: string | null;
  release_type?: ReleaseType | null;
}

export interface AlbumCreateIn {
  title: string;
  album_artist: string;
  year?: number | null;
  genre?: string | null;
  release_type?: ReleaseType | null;
}

export interface AlbumMergeIn {
  source_ids: number[];
  target_id: number;
}

export interface TracksMoveIn {
  track_ids: number[];
  album_id: number;
}

export interface TrackPatchIn {
  title?: string | null;
  artist?: string | null;
  track_no?: number | null;
  disc_no?: number | null;
  genre?: string | null;
  explicit?: boolean | null;
}

/** GET /api/starred: own typed decisions plus Navidrome/import snapshot fallbacks. */
export interface StarredOut {
  song_ids: string[];
  album_ids: string[];
  /** Own and imported pending track refs. Own subjects override this snapshot. */
  refs?: string[];
  artist_ids?: string[];
  subjects?: FavoriteSubject[];
}

export type FavoriteType = 'track' | 'album' | 'artist';

export interface FavoriteTarget {
  target_type: FavoriteType;
  /** a track: the Hub's id of it, if the Hub knows it */
  track_id?: number | null;
  ref?: string | null;
  nd_song_id?: string | null;
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  target_data?: Record<string, unknown> | null;
}

export interface FavoriteSubject {
  target_type: FavoriteType;
  key: string;
  ref?: string | null;
  nd_id?: string | null;
  liked: boolean;
  liked_since?: ISODate | null;
  event_id?: string | null;
  aliases: string[];
  target_data?: Record<string, unknown> | null;
  provenance: 'own' | 'import';
}

export interface StarOut extends OkOut {
  event_ids?: string[];
}

export interface StarIn {
  targets?: FavoriteTarget[];
  event_id?: string;
  ts?: ISODate;
  client?: string;
  context?: string;
  song_ids?: string[];
  album_ids?: string[];
  artist_ids?: string[];
  catalog_album_ref?: string | null;
  /** tracks of `StarredOut.refs`: only with `unstar` */
  refs?: string[];
  unstar?: boolean;
}

// --- search -----------------------------------------------------------------------------------

/** A line of «Недавнее» of the search: a result that was opened, or a query asked for with Enter. */
export interface SearchRecent {
  /** what the line is known by (to take it out) */
  key: string;
  kind: 'query' | 'track' | 'album' | 'artist';
  /** the query itself, or the name of what was opened */
  title: string;
  /** whose it is: the artist of a track or an album */
  subtitle?: string | null;
  ref?: string | null;
  cover_url?: string | null;
  /** the page a tap opens; none: the title is searched for */
  link?: string | null;
}

export interface SearchHistoryOut {
  items: SearchRecent[];
  /** «Вы часто ищете»: what was come back to more than once, the most often first */
  frequent: SearchRecent[];
}

export interface SearchOut {
  library: LibTrackOut[];
  artists: CatalogArtist[];
  albums: AlbumOut[];
  tracks: TrackOut[];
  /** offset of the next page of tracks (`api.searchTracks`); null — that's all */
  tracks_next?: number | null;
  errors: string[];
}

export interface SearchTracksOut {
  tracks: TrackOut[];
  next_offset?: number | null;
}

export interface ProviderSearchOut {
  candidates: Candidate[];
  errors: string[];
}

// --- downloads --------------------------------------------------------------------------------

export type DownloadStatus =
  | 'queued'
  | 'searching'
  | 'downloading'
  | 'tagging'
  | 'importing'
  | 'done'
  | 'failed'
  | 'needs_review'
  | 'cancelled';

export interface DownloadTrackIn {
  artist: string;
  title: string;
  album?: string | null;
  duration_s?: number | null;
  isrc?: string | null;
  explicit?: boolean | null;
  version?: string | null;
  catalog_ref?: string | null;
  candidate?: Candidate | null;
}

export interface DownloadIn {
  items?: DownloadTrackIn[];
  track_refs?: string[];
  album_ref?: string | null;
  artist_ref?: string | null;
  record_types?: string[];
  playlist_id?: string | null;
  /** several playlists at once: the songs go into each as they are imported */
  playlist_ids?: string[];
  new_playlist_name?: string | null;
  force?: boolean;
}

/** GET /api/listen: what plays for a catalog track before it is downloaded. */
export interface ListenOut {
  ref: string;
  /** full: the file a download would take; preview: Deezer's 30 s preview (no source has it); library: own file */
  kind: 'full' | 'preview' | 'library';
  artist: string;
  title: string;
  duration_s?: number | null;
  provider?: string | null;
  bitrate_kbps?: number | null;
  /** the file that plays: where it came from (a provider; `upload`), what it is (mp3, flac, opus…), and what it sounds
   * like when that is less than it says */
  source?: string | null;
  codec?: string | null;
  sounds_like_kbps?: number | null;
  /** an own file whose download kept its journal */
  journal?: boolean;
  page_url?: string | null;
  /** library: the Navidrome song (♥), once Navidrome has scanned the file */
  nd_song_id?: string | null;
  /** the Hub's track, if the Hub knows the song (tags, playlists) */
  track_id?: number | null;
  /** audio with Range support (same session cookie) */
  stream_url: string;
}

export interface DownloadBatchOut {
  batch_id: number;
  queued: number;
  already_owned: number;
  playlist_id?: string | null;
  /** catalog refs are expanded in the background; `queued` counts only explicit items */
  expanding?: boolean;
}

/** A line of a download's journal: what happened, and the file it is about. */
export interface DownloadStep {
  t: ISODateTime;
  kind: 'info' | 'ok' | 'warn' | 'bad';
  text: string;
  cand?: { artist: string; title: string; provider: string; bitrate_kbps?: number | null; duration_s?: number | null; page_url: string } | null;
}

export interface DownloadItemOut {
  id: number;
  batch_id: number;
  status: DownloadStatus | string;
  progress: number;
  artist: string;
  title: string;
  album?: string | null;
  provider?: string | null;
  error?: string | null;
  warnings: unknown[];
  track_id?: number | null;
  /** Navidrome song once imported and scanned */
  nd_song_id?: string | null;
  /** target playlist of the batch */
  playlist_id?: string | null;
  /** catalog track the item came from */
  track_ref?: string | null;
  /** queued by an import, a mix or the wave (nobody asked to download it): not shown as a download */
  quiet?: boolean;
  /** queued by this import (`/api/imports`) */
  import_id?: number | null;
  candidates?: Candidate[] | null;
  /** the journal (the detail view only): the steps of the download in order */
  log?: DownloadStep[] | null;
  chosen?: Candidate | null;
  /** taken off the list by the user (comes with an event: every open page drops it) */
  hidden?: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface RetryIn {
  candidate_index?: number | null;
  url?: string | null;
}

export interface ClearOut {
  removed: number;
}

// --- uploads ----------------------------------------------------------------------------------

export interface CoverInfo {
  hash?: string;
  w?: number;
  h?: number;
  format?: string | null;
  size?: number;
}

export interface UploadTags {
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  album_artist?: string | null;
  track_no?: number | null;
  disc_no?: number | null;
  year?: number | null;
  genre?: string | null;
  has_cover?: boolean;
  /** the picture inside the file: the mark of its bytes (one picture in two files is told by it), its size in
   * pixels, its kind and its weight */
  cover_info?: CoverInfo | null;
  duration_s?: number | null;
  [key: string]: unknown;
}

export interface UploadOut {
  id: number;
  orig_name: string;
  size: number;
  status: string;
  tags: UploadTags;
  error?: string | null;
  created_at: ISODateTime;
  /** the artists read out of the file's line of them, the main one first */
  artists?: string[];
  codec?: string | null;
  bitrate_kbps?: number | null;
  /** the library has this recording already */
  twin?: UploadTwin | null;
  /** what song of the catalog the file is */
  match?: UploadMatch | null;
  /** the file is heard to be poor */
  weak?: boolean;
  /** the file's own names: its tags and, where they are empty, its name */
  title: string;
  artist: string;
  /** its «album» is no album: nothing, «Single», the track's own name */
  single?: boolean;
  duration_s?: number | null;
}

/** The library's track an uploaded file is of already. */
export interface UploadTwin {
  track_id: number;
  title: string;
  artist: string;
  album?: string | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  /** told by the sound of the two files, or by their names and length */
  by: 'sound' | 'name';
  /** the same recording — or (false) only alike by its sound: a version in another language, a cover over the same
   * music */
  sure: boolean;
  /** the uploaded file is the better one */
  better: boolean;
}

/** A file to bring into the library. `catalog`: as the song of the catalog it was told to be (`UploadOut.match`) —
 * the fields below are not read then. Else by them: `album` empty (or the track's own name) makes a single. */
export interface UploadTrackIn {
  upload_id: number;
  catalog: boolean;
  /** another name the song is known by: the one its file had, kept by the listener's word */
  also_title?: string | null;
  /** the file is another recording of the song the catalog took it for: filed under its own names, tied to that
   * record as a version */
  version_of_match?: boolean;
  /** a record only like the file is not to be weighed against it by the sound afterwards */
  no_check?: boolean;
  title?: string;
  artist?: string;
  album?: string | null;
  year?: number | null;
  /** the library's track whose file this one takes the place of */
  replace_track_id?: number | null;
  /** the album of the request it goes into (`UploadCommitIn.albums`); none: a single */
  album_key?: string | null;
  /** a single's picture: the catalog's or the one in the file */
  cover?: 'catalog' | 'file' | null;
}

/** An album the files of a request go into, as it was chosen among what its files and the catalog say.
 * `release_of`: an upload whose catalog record stands on this release; the cover — the catalog's picture of an
 * upload's record, or the picture inside an uploaded file. */
export interface UploadAlbumIn {
  key: string;
  title: string;
  album_artist: string;
  year?: number | null;
  /** a single: one file, the «album» is the track itself */
  single?: boolean;
  release_of?: number | null;
  cover_of_match?: number | null;
  cover_of_file?: number | null;
}

/** The song of the catalog an uploaded file was told to be: `found` — it is that song; `doubt` — a record is like
 * the file but not surely it (`why`), used only when the listener says so; `none` — the catalog has nothing like it. */
export interface UploadMatch {
  state: 'found' | 'doubt' | 'none';
  why?: string | null;
  by_hand?: boolean;
  ref?: string | null;
  title?: string | null;
  artist?: string | null;
  artists?: string[];
  album?: string | null;
  album_artist?: string | null;
  /** everyone the release is credited to (the main one first) */
  album_artists?: string[];
  /** the release in the catalog: its page shows what it is */
  album_ref?: string | null;
  record_type?: string | null;
  year?: number | null;
  duration_s?: number | null;
  cover_url?: string | null;
  track_no?: number | null;
}

/** Albums that are one album in pieces («Мои файлы»): `target_id` — the one the rest are joined into. */
export interface SplitAlbums {
  title: string;
  target_id: number;
  albums: { id: number; title: string; album_artist: string; year?: number | null; track_count: number; has_cover: boolean }[];
}

export interface UploadCommitIn {
  tracks: UploadTrackIn[];
  albums?: UploadAlbumIn[];
  /** the playlists the imported tracks go into as well */
  playlist_ids?: string[];
  /** the imported tracks go into «Любимое» too */
  star?: boolean;
  /** ... and get these tags of one's own */
  tag_ids?: number[];
  /** the playlist that keeps what was uploaded together: the user's playlist of this name, made when there is none */
  keep_in?: string | null;
}

export interface UploadCommitOut {
  album_ids: number[];
  imported: number;
  /** the tracks they became */
  track_ids: number[];
  /** how many of them have a weak file: «Качество звука» looks for a better one */
  weak: number;
  /** how many were filed under their own names with a record only like them: the sound settles it there */
  unsure: number;
}

/** A track of «Качество звука»: a weak file a better one is looked for in the place of. `state`: queued, searching,
 * found (a file is kept aside for the listener to say), replaced, none (nothing better: `note` says why), kept. */
export interface QualityRow {
  id: number;
  track_id: number;
  title: string;
  artist: string;
  cover_url?: string | null;
  state: 'queued' | 'searching' | 'found' | 'replaced' | 'none' | 'kept';
  /** identify: whether the track is the catalog's record `record`, told by the sound */
  kind: 'better' | 'identify' | 'picked';
  record?: string | null;
  progress: number;
  /** the catalog knows the song */
  known: boolean;
  was_codec?: string | null;
  was_kbps?: number | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  likeness?: number | null;
  /** by the sound it is the same recording (though the two files may differ in length or at places) */
  sure?: boolean;
  /** the file found is the better one of the two */
  better?: boolean;
  provider?: string | null;
  note?: string | null;
  duration_s?: number | null;
  /** the file it had is still kept: both can be heard, the old one brought back */
  can_revert?: boolean;
  updated_at: string;
}

/** A track of the library and the file it plays from, as «Найти другой файл» shows it. */
export interface TrackFileOut {
  track_id: number;
  title: string;
  artist: string;
  duration_s?: number | null;
  cover_url?: string | null;
  explicit?: boolean | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  source?: string | null;
  name?: string | null;
}

/** How a file found for a track compares with the track's own: by the sound (`verdict`: same / alike / other /
 * unknown) and as a file. `taken`: it is the track's file now. */
export interface PickOut {
  likeness?: number | null;
  verdict: 'same' | 'alike' | 'other' | 'unknown';
  codec?: string | null;
  bitrate_kbps?: number | null;
  duration_s?: number | null;
  better: boolean;
  taken: boolean;
  /** where the two part though they are one recording: the track's file's moments, and which falls quiet there */
  gaps?: FileGap[];
  /** the file as it lies on the Hub for now: what it is heard by, who its source says it is, and the loudness of
   * the two along their lengths */
  key?: string | null;
  title?: string | null;
  artist?: string | null;
  provider?: string | null;
  peaks?: { mine?: number[]; other?: number[] };
  length?: { mine?: number | null; other?: number | null };
}

/** A place where two files of one recording part — what a censor's cut shows as: `at`…`to` in the first file (the
 * track's own, the upper lane), `other_at`…`other_to` in the second; `quieter` — which of them falls quiet there. */
export interface FileGap {
  at: number;
  to: number;
  other_at: number;
  other_to: number;
  depth: number;
  quieter?: 'mine' | 'other' | 'upper' | 'lower' | null;
}

export interface QualityPeaks {
  current: number[];
  found: number[];
  was: number[];
  gaps: FileGap[];
  length: Record<string, number | null>;
}

export interface QualityOut {
  rows: QualityRow[];
  /** weak files nothing was looked for yet */
  weak: number;
}

// --- playlists --------------------------------------------------------------------------------

export interface PlaylistOut {
  id: string;
  name: string;
  /** the tracks the library has */
  song_count: number;
  /** the playlist's tracks the library has no file of yet: they play, and are downloaded when listened to */
  pending_count?: number;
  /** the user's own: it can be renamed, given a picture, deleted («Микс дня» is the Hub's: it cannot) */
  editable?: boolean;
  duration_s: number;
  cover_art?: string | null;
  /** the playlist's picture, the user's own or Navidrome's (add `&size=`) */
  cover_url?: string | null;
}

export interface PlaylistOfSongOut extends PlaylistOut {
  /** the song is in this playlist */
  has: boolean;
}

export interface PlaylistOfSongsOut extends PlaylistOut {
  /** how many of the asked songs are in this playlist */
  count: number;
}

export interface PlaylistPageOut extends PlaylistOut {
  /** every track in its place; one the library has no file of yet has `owned: false` */
  tracks: NdSongOut[];
}

// --- the Navidrome library (the PWA in a browser: /api/nd) ------------------------------------

/** A library song or a catalog-only favorite (/nd/liked, without nd_song_id until downloaded). */
/** A track of «Все файлы»: the track as any list shows it (`added_at` — when its file came), and what the page
 * sorts it out by. */
export interface FileRowOut extends NdSongOut {
  /** who brought it */
  added_by?: string | null;
  mine: boolean;
  /** it is in one's «Любимое» or in a playlist of one's own */
  kept: boolean;
  no_album: boolean;
}

export interface NdSongOut extends TrackOut {
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  /** the library artists of `artists`, in their order */
  nd_artist_ids?: (string | null)[];
}

export interface NdAlbumOut {
  id: string;
  title: string;
  artist: string;
  artist_id?: string | null;
  year?: number | null;
  song_count: number;
  duration_s: number;
  cover_url?: string | null;
  starred: boolean;
  tracks?: NdSongOut[] | null;
}

export interface NdArtistOut {
  id: string;
  name: string;
  album_count: number;
  cover_url?: string | null;
  starred: boolean;
}

/** Everything an artist released, newest first, each release with its tracks. */
export interface DiscographyOut {
  artist: CatalogArtist;
  albums: AlbumOut[];
  /** releases said to be a namesake's: not among `albums` */
  excluded?: number;
  /** as `ArtistPageOut.profiles` */
  profiles?: string[];
}

export interface NdArtistPageOut {
  /** the same artist in the catalog: their page is the catalog's then */
  catalog_ref?: string | null;
  /** their tracks in the library: what the page of an artist the catalog does not know is drawn of */
  tracks?: NdSongOut[];
  artist: NdArtistOut;
  albums: NdAlbumOut[];
}

export interface NdLikedOut {
  songs: NdSongOut[];
  albums: NdAlbumOut[];
  artists: NdArtistOut[];
}

export interface NdHomeOut {
  recent: NdAlbumOut[];
  frequent: NdAlbumOut[];
  newest: NdAlbumOut[];
  starred: NdAlbumOut[];
  /** «Радиостанции»: liked artists first */
  radio: NdArtistOut[];
}

export type NdAlbumList = 'recent' | 'frequent' | 'newest' | 'starred' | 'alphabeticalByName' | 'alphabeticalByArtist' | 'random';

export interface PlaylistAddIn {
  /** the Hub's ids of the tracks */
  track_ids: number[];
}

// --- assistant --------------------------------------------------------------------------------

export type AssistantTaskStatus =
  | 'queued'
  | 'extracting'
  | 'resolving'
  | 'awaiting_confirmation'
  | 'confirmed'
  | 'done'
  | 'failed';

export type AssistantRowStatus = 'auto' | 'confirm' | 'not_found' | 'owned';

export interface AssistantChoice {
  kind: 'catalog' | 'library' | 'candidate' | 'manual' | 'skip';
  ref?: string | null;
  track_id?: number | null;
  candidate?: Candidate | null;
  artist?: string | null;
  title?: string | null;
}

export interface AssistantRowIn {
  index: number;
  selected: boolean;
  choice?: AssistantChoice | null;
}

export interface AssistantTargetIn {
  kind: 'new' | 'existing' | 'library';
  name?: string | null;
  playlist_id?: string | null;
}

export interface AssistantConfirmIn {
  confirm_token: string;
  rows: AssistantRowIn[];
  target: AssistantTargetIn;
}

export interface AssistantRowOut {
  index: number;
  artist_raw: string;
  title_raw: string;
  album_raw?: string | null;
  duration?: string | null;
  truncated: boolean;
  status: AssistantRowStatus;
  best?: TrackOut | null;
  options: TrackOut[];
  score: number;
  library_track_id?: number | null;
}

export interface AssistantTaskOut {
  id: number;
  status: AssistantTaskStatus | string;
  input_kind: string;
  source_app?: string | null;
  page_type?: string | null;
  context: Record<string, unknown>;
  rows: AssistantRowOut[];
  suggested_playlist_name?: string | null;
  confirm_token?: string | null;
  batch_id?: number | null;
  playlist_id?: string | null;
  error?: string | null;
  cost_usd: number;
  created_at: ISODateTime;
}

// --- assistant free chat (stage 3.8) --------------------------------------------------------------

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** What the assistant may not do without a tap: everything else it does itself (`ChatOut.done`). */
export type ChatAction =
  | { type: 'delete_playlist'; playlist_id: string; name: string }
  | { type: 'delete_library_tracks'; track_ids: number[]; titles: string[] };

export interface ChatOut {
  reply: string;
  cost_usd?: number;
  /** changes the assistant made this turn, written by the Hub */
  done?: string[];
  actions?: ChatAction[];
  task_id?: number | null;
  confirm_token?: string | null;
  summary?: string | null;
  /** the saved chat this turn went into */
  chat_id: number;
  title: string;
}

/** A saved chat in the history list. */
export interface ChatBriefOut {
  id: number;
  title: string;
  /** number of messages */
  messages: number;
  created_at: string;
  updated_at: string;
}

/** A message of a saved chat, as the Hub stored it (screenshots only by their count). */
export interface SavedChatMessage {
  role: 'user' | 'assistant';
  content: string;
  images?: number;
  cost?: number;
  done?: string[];
  proposal?: {
    task_id: number;
    token: string;
    summary: string;
    actions: ChatAction[];
    state: 'pending' | 'confirmed' | 'dismissed';
  };
}

export interface ChatSavedOut extends Omit<ChatBriefOut, 'messages'> {
  messages: SavedChatMessage[];
}

export interface ChatConfirmOut {
  ok: boolean;
  playlists_deleted: string[];
  tracks_deleted: number;
}

// --- imports from music services ------------------------------------------------------------

export type ImportSource = 'yandex' | 'spotify' | 'vk';
export type ImportMethod = 'device' | 'token' | 'profile' | 'csv' | 'password' | 'link';
export type ImportMode = 'economy' | 'full';
export type ImportStatus = 'auth' | 'fetching' | 'preview' | 'running' | 'done' | 'failed' | 'cancelled';
export type ImportCollectionKind = 'likes' | 'playlist' | 'albums' | 'artists' | 'dislikes';

export interface ImportIn {
  source?: 'yandex' | 'vk';
  method: ImportMethod;
  token?: string;
  url?: string;
  /** method `password` (VK): used for the sign-in only */
  login?: string;
  password?: string;
}

/** A sign-in in progress. `device` (Yandex): `user_code` is entered at `verification_url`. `code` (VK): the service
 * sent the user a code, it is typed in the Hub (`hint`: where it went; `wrong`: the last one did not fit). `captcha`
 * (VK): the page at `verification_url` has to be passed, what it answers with is given to the Hub. */
export interface ImportAuthOut {
  kind: 'device' | 'code' | 'captcha';
  user_code: string;
  verification_url: string;
  hint?: string | null;
  wrong: boolean;
  expires_at: ISODateTime;
}

export interface ImportStatsOut {
  tracks: number;
  owned: number;
  to_download: number;
  est_bytes: number;
  free_bytes: number | null;
  pending: number;
  exact: number;
  probable: number;
  not_found: number;
  waiting: number;
  downloading: number;
  done: number;
  failed: number;
}

export interface ImportCollectionOut {
  id: number;
  kind: ImportCollectionKind;
  title: string;
  selected: boolean;
  count: number;
  owned: number;
  cover_url?: string | null;
  /** the Hub's playlist made of the collection */
  playlist_id?: string | null;
}

export interface ImportOut {
  id: number;
  source: ImportSource;
  method: ImportMethod;
  status: ImportStatus | string;
  mode: ImportMode;
  account?: string | null;
  has_token: boolean;
  keep_token: boolean;
  download_albums: boolean;
  /** sign-in in progress */
  auth?: ImportAuthOut | null;
  error?: string | null;
  stats: ImportStatsOut;
  collections: ImportCollectionOut[];
  created_at: ISODateTime;
  started_at?: ISODateTime | null;
  finished_at?: ISODateTime | null;
}

export interface ImportStartIn {
  collection_ids: number[];
  mode: ImportMode;
  download_albums?: boolean;
  keep_token?: boolean;
  copy_likes?: boolean;
  /** «После переноса»: every transferred track, once the library has it, also gets a ♥ … */
  star?: boolean;
  /** … a place in these playlists … */
  playlist_ids?: string[];
  /** … and these tags */
  tag_ids?: number[];
}

export type ImportMatch = 'owned' | 'exact' | 'probable' | 'not_found';
export type ImportItemStatus = 'pending' | 'waiting' | 'queued' | 'done' | 'failed' | 'not_found' | 'skipped';

export interface ImportItemOut {
  id: number;
  collection_id: number;
  position: number;
  kind: 'track' | 'album' | 'artist';
  artist: string;
  title: string;
  album?: string | null;
  duration_s?: number | null;
  cover_url?: string | null;
  ref?: string | null;
  match?: ImportMatch | null;
  score?: number | null;
  status: ImportItemStatus | string;
  nd_song_id?: string | null;
  /** the Hub's track the item turned out to be */
  track_id?: number | null;
  download_item_id?: number | null;
  download_status?: string | null;
  error?: string | null;
}

export interface ImportItemsOut {
  items: ImportItemOut[];
  total: number;
}

export interface ImportEvent {
  id: number;
  status: string;
}

// --- events (SSE) -------------------------------------------------------------------------------

export interface AssistantEvent {
  id: number;
  status: string;
}

/** The user's ListenBrainz account (pages/ListenBrainz.tsx). */
export interface ListenBrainzOut {
  connected: boolean;
  user?: string | null;
  /** the last listen that went there, UTC */
  sent_until?: string | null;
  /** `send`: how many listens went this time */
  sent?: number;
}

/** A track's names as they are, what the catalog calls it and who changed it last (`components/TrackEdit.tsx`). */
export interface TrackEditOut {
  track_id: number;
  title: string;
  artist: string;
  catalog_title?: string | null;
  catalog_artist?: string | null;
  renamed: boolean;
  last_by?: string | null;
  last_at?: string | null;
  own_cover: boolean;
  cover_url?: string | null;
  /** its album of the library, whose cover can be changed too */
  album_id?: number | null;
  album_title?: string | null;
  has_file: boolean;
  /** who made the beat, where the track's name said so («(prod. by X)»): taken out of the name */
  producer?: string | null;
  /** the other names it is known by — the search reads them (`id` null: the catalog's, which cannot be dropped) */
  names?: TrackNameOut[];
}

/** A recording of a song: a track of its own, and what it is among the others (`components/Versions.tsx`). */
export interface VersionOut {
  track_id: number;
  title: string;
  artist: string;
  album?: string | null;
  duration_s?: number | null;
  /** what it is, in a word: «старая версия», «Deluxe» */
  label?: string | null;
  preferred: boolean;
  cover_url?: string | null;
  ref?: string | null;
  nd_song_id?: string | null;
  codec?: string | null;
  bitrate_kbps?: number | null;
  has_file: boolean;
}

/** The song a track is: every version of it, the preferred one first (one alone: the track is nobody's version). */
export interface VersionsOut {
  track_id: number;
  song_id?: number | null;
  title: string;
  versions: VersionOut[];
}

export interface TrackNameOut {
  id?: number | null;
  name: string;
  source: 'renamed' | 'file' | 'manual' | 'catalog' | string;
}

/** An artist as the Hub knows them: the one name, the other spellings, the projects linked, and who looks like the
 * same artist under another spelling. `rewriting`: the library is being written in the name just now. */
export interface ArtistCardOut {
  id: number;
  name: string;
  /** `id` null: the name of a namesake — not a spelling of theirs to change */
  names: { id: number | null; name: string; source: string; main: boolean }[];
  /** a namesake: bare text of this name is taken for that artist */
  held_by?: string | null;
  /** the catalog's records of the artist */
  profiles?: string[];
  projects: (ArtistBriefOut & { also: string[] })[];
  suggestions: ArtistBriefOut[];
  rewriting?: boolean;
}

/** The photos and the wide pictures an artist may be shown with, and which is chosen (none: as by itself — the
 * photo of the catalog's record the page is of, the wide picture the Hub finds). */
export interface ArtistLooksOut {
  id: number;
  name: string;
  photo?: string | null;
  banner?: string | null;
  photos: ArtistLookOption[];
  banners: ArtistLookOption[];
}

export interface ArtistLookOption {
  /** what is sent back to choose it */
  choice: string;
  label: string;
  note: string;
  url: string;
}

/** Another artist, as a page names them: the catalog's record that leads to their page, and a picture. */
export interface ArtistBriefOut extends ArtistPickOut {
  ref?: string | null;
  nb_fans?: number | null;
  picture_url?: string | null;
}

export interface ArtistPickOut {
  id: number;
  name: string;
}

/** Another artist: one the Hub knows, or a name. */
export interface ArtistOtherIn {
  other_id?: number;
  name?: string;
}

/** A list the user played from lately («Продолжить» of the home): where it leads and how it is drawn. */
export interface RecentPlaceOut {
  /** the address of its page */
  to: string;
  kind: 'liked' | 'playlist' | 'album' | 'artist' | 'for-you' | 'selection';
  title: string;
  cover_url?: string | null;
  /** the mark drawn where it has no picture */
  icon: string;
  /** an artist: the picture is a circle */
  round?: boolean;
}

/** A release of a followed artist («Подписки», pages/Subscriptions.tsx). */
export interface ReleaseOut {
  album_ref: string;
  title: string;
  artist: string;
  artist_ref: string;
  record_type: string;
  /** YYYY-MM-DD */
  release_date: string;
  cover_url?: string | null;
  nb_tracks?: number | null;
  /** not seen before this opening of the feed */
  new: boolean;
  /** its tracks; null: the catalog has not been asked for them yet */
  tracks?: TrackOut[] | null;
}

/** An artist the user follows. */
export interface FollowOut {
  /** the artist in the catalog */
  ref: string;
  name: string;
  /** what the follow itself is kept by: to unfollow by */
  like_ref?: string | null;
  nd_id?: string | null;
  picture_url?: string | null;
  /** the latest thing they released, however long ago (YYYY-MM-DD) */
  last_release_date?: string | null;
  last_release_title?: string | null;
  /** how many of their releases the user has not seen */
  new: number;
}

export interface ReleasesOut {
  /** how many artists are followed */
  artists: number;
  unseen: number;
  /** the ones with something new first, then by their latest release */
  following: FollowOut[];
  items: ReleaseOut[];
}

export interface LibraryEvent {
  kind: 'imported' | 'changed' | 'starred' | 'unstarred';
  album_id?: number | null;
  track_id?: number | null;
  /** starred / unstarred: songs the Hub (an import, a download queued with ♥, the assistant) starred or unstarred */
  song_ids?: string[];
}

export interface BatchEvent {
  batch_id: number;
  queued: number;
}

export interface HubEventMap {
  download: DownloadItemOut;
  assistant: AssistantEvent;
  library: LibraryEvent;
  batch: BatchEvent;
  import: ImportEvent;
  search_history: Record<string, never>;
  ping: Record<string, never>;
  /** Pseudo event: the stream (re)connected after a drop; refetch state. */
  reconnect: Record<string, never>;
}

// --- misc ---------------------------------------------------------------------------------------

export interface HealthOut {
  capabilities?: string[];
  ok: boolean;
  navidrome: boolean;
  providers: Record<string, unknown>;
  version: string;
  today?: ISODate | null;
}

// --- listening data (MIXES.md 3.2) -----------------------------------------------------------------

export type PlayKind = 'start' | 'progress' | 'interrupted' | 'error' | 'skip' | 'finish' | 'like' | 'unlike' | 'dislike' | 'undislike';

export interface PlayEventIn {
  target_type?: FavoriteType;
  /** a track: the Hub's id of it, if the Hub knows it */
  track_id?: number | null;
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  target_data?: Record<string, unknown> | null;
  reverts_event_id?: string | null;
  event_id: string;
  /** ISO 8601, UTC */
  ts: string;
  kind: PlayKind;
  ref?: string | null;
  nd_song_id?: string | null;
  playback_id?: string | null;
  device_id?: string | null;
  listened_ms?: number | null;
  track_metadata?: { title?: string; album?: string; artist_credit?: string; artists?: { name: string; ref?: string }[]; duration_s?: number | null; isrc?: string; year?: number } | null;
  position_s?: number | null;
  duration_s?: number | null;
  /** where the list was started from: album, playlist, search, liked, queue, other (mix:<id>, wave:<id> later) */
  context?: string | null;
  /** the page the list was started on (`/playlist/12`): «История» names the list by it */
  page?: string | null;
  client: string;
  /** analytics only (INSIGHTS.md 6.5): one id for every event of a launched list (`wave:<id>` for a wave) */
  launch_id?: string | null;
  /** where the list was launched: `mix_page`, `mix_page_track`, `album`, `search`, `wave`… */
  launch_origin?: LaunchOrigin | null;
}

/** Where in the interface a list or a wave was launched (INSIGHTS.md 6.3, 6.5): `^[a-z][a-z0-9_]*$`, ≤ 32. */
export type LaunchOrigin = string;

export interface PlayEventsOut {
  accepted: number;
  duplicates: number;
  rejected: { index: number; event_id?: string | null; detail: string }[];
}

export interface DislikeIn {
  track_id?: number | null;
  ref?: string | null;
  nd_song_id?: string | null;
  artist_ref?: string | null;
  artist?: string | null;
  title?: string | null;
  context?: string | null;
  client?: string;
}

export interface DislikeOut {
  id: number;
  /** the Hub's track, if the Hub knows the song */
  track_id?: number | null;
  ref: string | null;
  nd_song_id: string | null;
  artist_ref: string | null;
  artist: string | null;
  title: string | null;
  source: 'app' | 'pwa' | 'yandex_import' | string;
  created_at: string;
  /** of an artist's mark: everything the artist is known by — every record a catalog keeps of them and every
   * spelling of the name. The mark is of all of it */
  artist_refs?: string[];
  artist_names?: string[];
}

// --- «Микс дня» and the taste (MIXES.md 6) ----------------------------------------------------------

export type MixMode = 'normal' | 'favorites' | 'discover';

export interface MixTrackOut extends TrackOut {
  position: number;
  /** the strongest source of the song: favorites, stash, ytm_radio, lastfm_similar… */
  generator: string;
  generator_title: string;
  generators: string[];
  /** familiar to the user or new */
  familiar: boolean;
  score: number;
  /** closeness to the taste by sound; null — not known */
  sim?: number | null;
}

export interface MixOut {
  id: number;
  title: string;
  description: string;
  mode: MixMode;
  mode_title: string;
  created_at: ISODateTime;
  nd_playlist_id?: string | null;
  /** songs in the library already; the rest download quietly */
  ready: number;
  total: number;
  tracks: MixTrackOut[];
  generators: Record<string, unknown>;
}

export interface MixSettingsOut {
  mode: MixMode;
  mix_hour: number;
  timezone: string;
  /** local time */
  next_build_at: ISODateTime;
  nd_playlist_id?: string | null;
  artists: Array<{ ref: string; name: string }>;
}

/** A listen of «История» (`/api/plays/history`). */
export interface HistoryItem {
  /** names the listen: what «убрать из истории» is told */
  uid: string;
  ts: ISODateTime;
  /** the day it is of, in the Hub's time zone (YYYY-MM-DD) */
  day: string;
  /** listened */
  ms: number;
  duration_ms?: number | null;
  /** played long enough to count; false: left early */
  finished: boolean;
  title: string;
  artist: string;
  /** where it was played from, and the page of that list when the Hub knows which */
  from?: string | null;
  from_to?: string | null;
  origin: string;
  /** the library's track, when it has one */
  track?: TrackOut | null;
  /** a catalog track the library has not got: it plays by this */
  ref?: string | null;
}

export interface HistoryOut {
  items: HistoryItem[];
  /** the totals of the days the items are of */
  days: Record<string, { count: number; ms: number }>;
  total: number;
  next_offset?: number | null;
}

/** A list of «Для вас» (`/api/for-you`): `mix`, `weekly` or a kind of the lists of a rule. */
export interface ForYouItem {
  kind: string;
  title: string;
  /** tracks it has now; null: nothing was made yet */
  count: number | null;
  /** a mix or an issue made since the user last opened the list */
  fresh?: boolean;
}

/** A change somebody made to a track (`components/TrackEdit.tsx`): each field as it was and as it became. */
export interface TrackEditEntry {
  id: number;
  by?: string | null;
  at: string;
  changes: Record<string, [unknown, unknown]>;
}

/** A track whose file was deleted lately and is still kept («Корзина», pages/Trash.tsx). */
export interface TrashedOut {
  id: number;
  title: string;
  artist: string;
  album?: string | null;
  deleted_by?: string | null;
  deleted_at: string;
  days_left: number;
  /** how many playlists it stood in */
  playlists: number;
}

/** A list of «Для вас» as its page shows it: the tracks, and what is the list's own beside them. */
export interface ForYouOut {
  kind: string;
  title: string;
  /** the player's context of a list started from here */
  context: string;
  tracks: TrackOut[];
  /** one more thing to say under the name: «скачано 32», «прежний микс» */
  note?: string | null;
  /** the list is being made: ask again this soon (seconds) */
  refresh_s?: number | null;
  /** a top: Navidrome song id -> the listens its rule counted */
  plays?: Record<string, number> | null;
  plays_label?: string | null;
  mix?: { id: number | null; current: boolean; building: boolean; settings: MixSettingsOut; onboarding: boolean } | null;
  weekly?: { issue: number | null; week: string | null; latest: boolean; building: boolean; issues: { id: number; week: string; count: number }[] } | null;
}

export interface MixTodayOut {
  mix: MixOut | null;
  /** a build is queued or running */
  building: boolean;
  settings: MixSettingsOut;
  /** no history, no picked artists and no mix: show «Что вы слушаете?» */
  onboarding: boolean;
}

export interface MixSettingsIn {
  mode?: MixMode;
  mix_hour?: number;
}

/** «Карта»: an artist and those Last.fm holds to be near them, nearest first; `match` 0…1 */
export interface ArtistMapOut {
  center: CatalogArtist;
  around: Array<{ artist: CatalogArtist; match: number }>;
  /** asking for more may give more */
  more?: boolean;
}

export interface TasteOnboardingOut {
  needed: boolean;
  selected: Array<{ ref: string; name: string }>;
  suggestions: CatalogArtist[];
  /** what the suggestions are: those this server listens to, the catalog's chart (a server without music), the search */
  source: 'server' | 'chart' | 'search';
}

// «Моя волна» (MIXES.md 7, hub/API.md «Моя волна»)

/** What a wave starts from («Волна по треку»): one of them. */
export interface WaveSeedIn {
  ref?: string | null;
  nd_song_id?: string | null;
  artist_ref?: string | null;
  nd_artist_id?: string | null;
  by_artist?: boolean;
}

export interface WaveStartIn {
  seed?: WaveSeedIn | null;
  mode?: MixMode | null;
  /** analytics only: where it was started (`home_card`, `catalog_card`, `track_menu`, `artist_menu`, `player`,
   * `artist_page`, `radio`, `search_file`; the app's `restart`), the client (`pwa`, `pwa-app`) */
  origin?: WaveOrigin | null;
  client?: string | null;
  restart_of?: string | null;
}

export type WaveOrigin = 'home_card' | 'catalog_card' | 'track_menu' | 'artist_menu' | 'player' | 'artist_page' | 'radio'
  | 'search_file' | 'restart';

export interface WaveNextIn {
  /** the events since the previous call (the same ones go to /api/plays/events: one event_id is stored once) */
  played: PlayEventIn[];
  /** refs of the wave's tracks still ahead in the player, not started: the answer replaces them */
  queued: string[];
}

export interface WaveTrackOut extends TrackOut {
  generator: string;
  generator_title: string;
  familiar: boolean;
  sim?: number | null;
}

// «Умное перемешивание» (MIXES.md 8, hub/API.md «Умное перемешивание»)

export interface SmartStartIn {
  /** `liked` or `playlist:<Navidrome id>` */
  context: string;
  /** the context's songs in its order (a playlist: new ones at the end; «Любимое»: the newest first): a Navidrome
   * song id, or a catalog ref for a ♥ not downloaded yet */
  songs: string[];
  /** the song tapped or playing now: it plays first */
  current?: string | null;
  title?: string | null;
  origin?: string | null;
  client?: string | null;
}

export interface SmartNextIn {
  played: PlayEventIn[];
  /** the own song the player is at */
  at?: string | null;
}

/** A recommendation: it goes right after the own song `after` (an id from `SmartOut.own`). */
export interface SmartRecOut extends WaveTrackOut {
  after: string;
  slot: number;
}

export interface SmartOut {
  session_id: string;
  context: string;
  title?: string | null;
  /** the own songs in the order to play (start only) */
  own: string[];
  recs: SmartRecOut[];
  pending: boolean;
  own_per_rec: number;
  recorded?: PlayEventsOut | null;
}

export interface SmartAddOut {
  ok: boolean;
  /** added to the playlist / downloading and will be added / «Любимое»: the client likes it itself */
  status: 'added' | 'downloading' | 'liked';
  playlist_id?: string | null;
}

export interface WaveOut {
  session_id: string;
  title: string;
  mode: MixMode;
  seed?: { ref?: string; artist_ref?: string; nd_artist_id?: string; artist?: string; title?: string } | null;
  /** up to 5: they replace `queued` */
  tracks: WaveTrackOut[];
  /** sources still answer: an empty `tracks` — ask again in a few seconds */
  pending: boolean;
  recorded?: PlayEventsOut | null;
}
